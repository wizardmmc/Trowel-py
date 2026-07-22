"""In-memory cross-provider quota read model (slice-093-pre).

Holds the latest ``QuotaSnapshot`` per ``(provider, account_id)`` and serves it
to the WorkBroker (093) and the frontend. Not persisted: on restart the model
is empty until the first GLM poll / Codex push.

A snapshot whose ``fetched_at`` ages past ``stale_after_ms`` is served as
``STALE`` (windows kept, so the value is still visible but flagged) — the
honest answer when the GLM poll missed a cycle or Codex has not pushed during
a turn. Error statuses are NOT relabeled stale (they are already not-ok).

Concurrency: single-threaded asyncio. ``update`` is a synchronous dict write
with no ``await`` inside, so it is atomic w.r.t. the event loop.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import replace

from trowel_py.quota.types import Provider, QuotaSnapshot, QuotaStatus

#: default age after which an OK snapshot is served as STALE (10 minutes;
#: comfortably past the 5-minute GLM poll cadence).
DEFAULT_STALE_AFTER_MS = 600_000


def _default_now_ms() -> int:
    """Epoch milliseconds."""

    return int(time.time() * 1000)


class QuotaReadModel:
    """Latest quota snapshot per (provider, account_id), with staleness.

    Args:
        now_ms: epoch-ms clock (injectable for fake clocks).
        stale_after_ms: age beyond which an OK snapshot is served as STALE.
    """

    def __init__(
        self,
        *,
        now_ms: Callable[[], int] | None = None,
        stale_after_ms: int = DEFAULT_STALE_AFTER_MS,
    ) -> None:
        self._latest: dict[tuple[Provider, str], QuotaSnapshot] = {}
        self._now_ms = now_ms or _default_now_ms
        self._stale_after_ms = stale_after_ms

    def update(self, snapshot: QuotaSnapshot) -> None:
        """Record the latest snapshot for its (provider, account_id)."""

        self._latest[(snapshot.provider, snapshot.account_id)] = snapshot

    def get(self, provider: Provider, account_id: str) -> QuotaSnapshot | None:
        """Latest snapshot, flagged STALE if an OK snapshot has aged out.

        ``None`` when this account has never reported.
        """

        snap = self._latest.get((provider, account_id))
        if snap is None:
            return None
        if (
            snap.status is QuotaStatus.OK
            and int(self._now_ms()) - snap.fetched_at > self._stale_after_ms
        ):
            return replace(snap, status=QuotaStatus.STALE)
        return snap

    def all(self) -> tuple[QuotaSnapshot, ...]:
        """Every known account's snapshot (staleness applied), insertion order.

        Iterates a copy of the keys (``dict.copy`` is one C call) so an
        off-event-loop reader cannot trip "dictionary changed size during
        iteration" when a writer (scheduler / observer) mutates concurrently.
        """

        return tuple(
            snap
            for key in tuple(self._latest.copy())
            if (snap := self.get(*key)) is not None
        )

    def mark_stale(self, provider: Provider, account_id: str) -> None:
        """Flip a currently-OK snapshot to STALE in place (windows kept).

        Used by the Codex observer on turn-terminal events: the account rate
        limit is no longer being actively refreshed, so serve it as stale until
        the next turn's push (slice-093-pre criterion 4: "turn 间 stale").
        Error/no-data snapshots are left alone.
        """

        snap = self._latest.get((provider, account_id))
        if snap is not None and snap.status is QuotaStatus.OK:
            self._latest[(provider, account_id)] = replace(
                snap, status=QuotaStatus.STALE
            )
