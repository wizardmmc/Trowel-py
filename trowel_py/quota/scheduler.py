"""GLM quota polling scheduler (slice-093-pre).

One background task polls every configured GLM account's Coding Plan quota on a
fixed cadence and feeds the snapshots into the ``QuotaReadModel``. Mirrors the
memory-scheduler pattern (``tidy_scheduler`` / ``review_scheduler``):
injectable ``sleep_fn`` for fake-clock tests, idempotent ``start`` / ``stop``,
swallowed per-poll failures so one bad account never kills the loop.

Design: a SINGLE sequential task, not one task per account. Within a cycle it
polls account 0 immediately, then waits ``stagger_s`` before each subsequent
account, so the provider never sees simultaneous hits. The first cycle runs
immediately on ``start`` (the read model is populated ASAP), then every cycle
is paced by ``interval_s`` (default 5 minutes).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit

from trowel_py.quota.read_model import QuotaReadModel
from trowel_py.quota.types import QuotaSnapshot

logger = logging.getLogger(__name__)

#: default poll interval (5 minutes). The Coding Plan 5h/weekly windows move
#: slowly; 5 min is the cadence the user confirmed and what KiwiGaze uses.
DEFAULT_INTERVAL_S = 300.0
#: default stagger between accounts within one cycle — keeps the provider from
#: seeing simultaneous requests.
DEFAULT_STAGGER_S = 1.0


class QuotaFetcher(Protocol):
    """Anything with an async ``fetch(account_id, api_key) -> QuotaSnapshot``.

    Production binds ``GlmQuotaClient``; tests bind a fake.
    """

    async def fetch(self, account_id: str, api_key: str) -> QuotaSnapshot:  # pragma: no cover - typing only
        ...


@dataclass(frozen=True)
class GlmAccount:
    """One GLM Coding Plan account slot.

    Attributes:
        account_id: stable slot label (e.g. ``glm-a``); provider-scoped.
        api_key: the raw Coding Plan api key (sent verbatim as Authorization).
        host: scheme + host; defaults to the China station.
    """

    account_id: str
    api_key: str
    host: str = "https://open.bigmodel.cn"


class QuotaScheduler:
    """Polls configured GLM accounts on a fixed cadence into a read model.

    Args:
        accounts: the GLM account slots to poll (one key each).
        client: the quota fetcher (real: ``GlmQuotaClient``; tests: a fake).
        read_model: where each snapshot is recorded.
        interval_s: time between full cycles (default 5 minutes).
        stagger_s: delay between accounts within one cycle.
        sleep_fn: injectable async sleep (fake-clock tests).
    """

    def __init__(
        self,
        accounts: Sequence[GlmAccount],
        client: QuotaFetcher,
        read_model: QuotaReadModel,
        *,
        interval_s: float = DEFAULT_INTERVAL_S,
        stagger_s: float = DEFAULT_STAGGER_S,
        sleep_fn: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._accounts: tuple[GlmAccount, ...] = tuple(accounts)
        self._client = client
        self._read_model = read_model
        self._interval_s = interval_s
        self._stagger_s = stagger_s
        self._sleep = sleep_fn or asyncio.sleep
        self._tasks: list[asyncio.Task[None]] = []
        self._started = False
        self._stopping = False

    @property
    def tasks(self) -> tuple[asyncio.Task[None], ...]:
        """Snapshot of live scheduler tasks (for tests / shutdown inspection)."""

        return tuple(self._tasks)

    async def start(self) -> None:
        """Launch the poll loop. No-op if already started."""

        if self._started:
            return
        self._started = True
        self._stopping = False
        logger.info(
            "[quota] scheduler started (%d account(s), every %ss)",
            len(self._accounts),
            self._interval_s,
        )
        self._tasks.append(asyncio.create_task(self._loop(), name="quota-poll"))

    async def stop(self) -> None:
        """Cancel the poll loop and await cleanup."""

        self._stopping = True
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self._started = False

    async def _loop(self) -> None:
        """Poll every account per cycle, staggered, paced by ``interval_s``."""

        while not self._stopping:
            for index, account in enumerate(self._accounts):
                if self._stopping:
                    break
                if index > 0:
                    await self._sleep(self._stagger_s)
                await self._poll_once(account)
            if self._stopping:
                break
            await self._sleep(self._interval_s)

    async def _poll_once(self, account: GlmAccount) -> None:
        """Fetch one account and record the snapshot; never raises."""

        try:
            snapshot = await self._client.fetch(account.account_id, account.api_key)
        except Exception:
            # GlmQuotaClient already maps known failures to statuses; this only
            # guards unexpected errors so one account can't kill the loop.
            logger.warning(
                "[quota] glm poll raised for %s", account.account_id, exc_info=True
            )
            return
        self._read_model.update(snapshot)


def load_glm_accounts(config: object | None = None) -> list[GlmAccount]:
    """Read the active GLM Coding Plan account from ``config.toml``.

    Returns one ``GlmAccount`` when the active llm group targets bigmodel.cn
    with a real (non-placeholder) key; otherwise ``[]`` — no scheduler, no
    fetches. Multi-account config is deferred to a later slice; v0 binds the
    single configured account under the id ``"glm"``.
    """

    if config is None:
        from trowel_py.config import load_llm_config

        try:
            config = load_llm_config()
        except Exception:
            logger.warning("[quota] could not load llm config", exc_info=True)
            return []
    base_url = getattr(config, "base_url", "") or ""
    api_key = getattr(config, "api_key", "") or ""
    parts = urlsplit(base_url)
    # Strict host check: a substring test ("bigmodel.cn" in base_url) would also
    # accept a deceptive host like "open.bigmodel.cn.attacker.invalid" and leak
    # the raw api key there. Require https + an exact allowed hostname.
    allowed_hosts = {"open.bigmodel.cn"}
    if (
        parts.scheme != "https"
        or parts.hostname not in allowed_hosts
        or not api_key
        or api_key.startswith("<")
    ):
        return []
    host = f"{parts.scheme}://{parts.netloc}"
    return [GlmAccount(account_id="glm", api_key=api_key, host=host)]
