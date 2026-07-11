"""sessions registry for the memory write loop (slice-040 T3).

A private sqlite db (``~/.trowel/memory/meta/sessions.db``) records every CC
session at session-start (registered by ``cc_host.service``), so the daily
review job can find a day's sessions by date without scanning the filesystem
(mtime drifts when cc ``--resume`` rewrites a jsonl). Memory owns this db (D3)
— it is deliberately separate from the cwd ``trowel.db`` so the write loop
stays decoupled from any one project's working directory.

Schema is self-managed here (``CREATE TABLE IF NOT EXISTS``); it does NOT live
in ``db/migrations/`` (that is the trowel.db migration chain).
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

_META_DIR = "meta"
_SESSIONS_DB = "sessions.db"

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS sessions (
    cc_session_id TEXT PRIMARY KEY,
    workdir       TEXT NOT NULL,
    date          TEXT NOT NULL,
    jsonl_path    TEXT,
    registered_at TEXT NOT NULL,
    extracted_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_date ON sessions(date);
"""


@dataclass(frozen=True)
class SessionRecord:
    """One registered CC session (a write-loop candidate).

    Attributes:
        cc_session_id: cc's uuid session id (the jsonl filename stem).
        workdir: the session's working directory (used to exclude the
            distillation sessions themselves — D2).
        date: ISO ``YYYY-MM-DD`` the session started on.
        jsonl_path: absolute path to the cc session jsonl.
        registered_at: timestamp of registration.
        extracted_at: when the daily review extracted this session (None =
            pending).
    """

    cc_session_id: str
    workdir: str
    date: str
    jsonl_path: str = ""
    registered_at: str = ""
    extracted_at: str | None = None


class SessionsRepository:
    """CRUD over the sessions table. Holds one sqlite connection."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_CREATE_SQL)
        self._conn.commit()

    def register(self, rec: SessionRecord) -> None:
        """Idempotent insert (PK). Re-registering a session_id is a no-op.

        cc's session-start hook (``service.py``) may fire more than once for a
        session; the PK keeps this harmless.
        """
        self._conn.execute(
            "INSERT OR IGNORE INTO sessions"
            " (cc_session_id, workdir, date, jsonl_path, registered_at, extracted_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                rec.cc_session_id,
                rec.workdir,
                rec.date,
                rec.jsonl_path,
                rec.registered_at,
                rec.extracted_at,
            ),
        )
        self._conn.commit()

    def find_pending(
        self, date: str, exclude_workdir_substr: str = ""
    ) -> list[SessionRecord]:
        """Return sessions of ``date`` not yet extracted.

        Args:
            date: ISO ``YYYY-MM-DD``.
            exclude_workdir_substr: if set, skip sessions whose workdir contains
                this substring (e.g. ``"review-daily-work"`` to skip the
                distillation sessions themselves — D2, prevents self-recursion).
                Must NOT contain LIKE wildcards (``%`` / ``_``) — it is matched
                via SQL ``LIKE``; the sole caller passes a literal path segment.

        Returns:
            pending sessions, oldest first (stable extraction order).
        """
        if exclude_workdir_substr:
            rows = self._conn.execute(
                "SELECT * FROM sessions"
                " WHERE date = ? AND extracted_at IS NULL"
                " AND workdir NOT LIKE ?"
                " ORDER BY registered_at",
                (date, f"%{exclude_workdir_substr}%"),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM sessions"
                " WHERE date = ? AND extracted_at IS NULL"
                " ORDER BY registered_at",
                (date,),
            ).fetchall()
        return [_row_to_record(r) for r in rows]

    def find_by_date(self, date: str) -> list[SessionRecord]:
        """Return ALL sessions of ``date`` (extracted or not), oldest first.

        slice-040-a repair uses this to replay a day's drafts into episodes
        regardless of extracted_at — the repair reads surviving drafts, not the
        pending queue.
        """
        rows = self._conn.execute(
            "SELECT * FROM sessions WHERE date = ? ORDER BY registered_at",
            (date,),
        ).fetchall()
        return [_row_to_record(r) for r in rows]

    def mark_extracted(self, cc_session_id: str, when: str) -> None:
        """Stamp extracted_at on a session after the review extracted it."""
        self._conn.execute(
            "UPDATE sessions SET extracted_at = ? WHERE cc_session_id = ?",
            (when, cc_session_id),
        )
        self._conn.commit()


def create_sessions_repository(conn: sqlite3.Connection) -> SessionsRepository:
    """Factory mirroring the cards/review repository pattern."""
    return SessionsRepository(conn)


def open_sessions_db(memory_root: Path) -> sqlite3.Connection:
    """Open (creating the meta dir if needed) sessions.db under memory_root."""
    meta = memory_root / _META_DIR
    meta.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(meta / _SESSIONS_DB))
    conn.row_factory = sqlite3.Row
    return conn


def _row_to_record(row: sqlite3.Row) -> SessionRecord:
    return SessionRecord(
        cc_session_id=row["cc_session_id"],
        workdir=row["workdir"],
        date=row["date"],
        jsonl_path=row["jsonl_path"] or "",
        registered_at=row["registered_at"],
        extracted_at=row["extracted_at"],
    )
