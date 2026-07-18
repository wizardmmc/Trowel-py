"""attribution resolution for access/outcome records (slice-061).

Resolves a retrieval record — which carries a ``trowel_session_id`` plus a
possibly-empty ``cc_session_id`` — to a cc session + kind, so judge/metrics no
longer depend on a non-empty ``cc_session_id`` at write time. 842 of 845 real
access-log rows had an empty ``cc_session_id`` because a fresh cc's MCP
subprocess starts before cc init emits the cc session id; this module is what
lets those rows still be attributed.

Resolution order (C-3: identity must not depend on init timing):

1. ``trowel_session_id`` → ``session_bindings`` row (cc_id + kind). The primary
   attribution key — works even when the record's ``cc_session_id`` is empty.
2. fall back to a non-empty ``cc_session_id`` → ``sessions`` row kind
   (legacy records that predate bindings; ``unknown`` kind when the cc id is
   not in the sessions table).
3. neither → ``unattributed`` (counted into coverage, excluded from user rates
   — C-7: never guess ownership for a record with no verifiable mapping).

``AttributionIndex`` loads the two tables once (two SELECTs) and resolves in
memory, so a metrics run over thousands of records costs O(records), not
O(records × db-roundtrips).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from trowel_py.memory.sessions_repo import (
    SessionBinding,
    SessionsRepository,
    create_sessions_repository,
    open_sessions_db,
)

AttributionBasis = Literal["trowel_binding", "cc_session_id", "unattributed"]


@dataclass(frozen=True)
class Attribution:
    """The resolved owner of one retrieval record (slice-061).

    Attributes:
        cc_session_id: the cc session this record belongs to, or None when
            unattributed.
        session_kind: user / review / distill / eval — from the binding or the
            sessions row; ``unknown`` when the cc id is not registered.
        basis: which resolution path succeeded (``unattributed`` when none).
    """

    cc_session_id: str | None
    session_kind: str
    basis: AttributionBasis

    @property
    def attributed(self) -> bool:
        """True iff a cc session was resolved (excludes unattributed rows)."""
        return self.basis != "unattributed"

    @property
    def is_user(self) -> bool:
        """True iff this is an attributed USER session (the metrics population).

        Non-user (review/distill/eval) and unattributed records are excluded
        from read_rate (C-3 — the judge's own eval reads must not count).
        """
        return self.attributed and self.session_kind == "user"


class AttributionIndex:
    """In-memory batch resolver over the binding + sessions tables.

    Build once per judge/metrics run (``from_repo`` / ``from_root``), then call
    ``resolve`` per record. An empty index (missing/unreadable db) resolves
    every record via the cc_session_id fallback or to unattributed — it never
    raises, so a memory-subsystem failure cannot blank the metrics.
    """

    def __init__(
        self,
        by_trowel: dict[str, SessionBinding],
        cc_kinds: dict[str, str],
    ) -> None:
        self._by_trowel = by_trowel
        self._cc_kinds = cc_kinds

    @classmethod
    def empty(cls) -> "AttributionIndex":
        return cls({}, {})

    @classmethod
    def from_repo(cls, repo: SessionsRepository) -> "AttributionIndex":
        """Build from an open repository (two SELECTs, in-memory thereafter)."""
        by_trowel = {b.trowel_session_id: b for b in repo.all_bindings()}
        return cls(by_trowel, repo.all_cc_kinds())

    @classmethod
    def from_root(cls, root: Path | str) -> "AttributionIndex":
        """Build from a memory root, tolerating a missing/unreadable sessions db.

        A missing or unreadable db degrades to an empty index: records with a
        non-empty cc_session_id still attribute via the fallback, the rest go
        unattributed. Read-only: when the db file is absent we do NOT create it
        (a dry-run / metrics pass must have no filesystem side effect).
        """
        if not (Path(root) / "meta" / "sessions.db").exists():
            return cls.empty()
        try:
            conn = open_sessions_db(Path(root))
        except Exception:
            return cls.empty()
        try:
            return cls.from_repo(create_sessions_repository(conn))
        except Exception:
            return cls.empty()
        finally:
            conn.close()

    def resolve(
        self, trowel_session_id: str, cc_session_id: str
    ) -> Attribution:
        """Resolve one record to its cc session + kind (C-3 order)."""
        if trowel_session_id:
            binding = self._by_trowel.get(trowel_session_id)
            if binding is not None:
                return Attribution(
                    cc_session_id=binding.cc_session_id,
                    session_kind=binding.session_kind,
                    basis="trowel_binding",
                )
        if cc_session_id:
            return Attribution(
                cc_session_id=cc_session_id,
                session_kind=self._cc_kinds.get(cc_session_id, "unknown"),
                basis="cc_session_id",
            )
        return Attribution(
            cc_session_id=None, session_kind="unknown", basis="unattributed"
        )

    def trowel_ids_for_cc(self, cc_session_id: str) -> set[str]:
        """Every trowel id bound to this cc id (C-4 — many-to-one).

        Used by the judge to gather ALL retrieval records of one cc session,
        including those written under a different trowel id across a resume.
        """
        return {
            b.trowel_session_id
            for b in self._by_trowel.values()
            if b.cc_session_id == cc_session_id
        }
