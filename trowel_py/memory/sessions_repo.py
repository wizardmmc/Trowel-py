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
from datetime import datetime
from pathlib import Path
from typing import Protocol, runtime_checkable

_META_DIR = "meta"
_SESSIONS_DB = "sessions.db"

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS sessions (
    cc_session_id         TEXT PRIMARY KEY,
    workdir               TEXT NOT NULL,
    date                  TEXT NOT NULL,
    jsonl_path            TEXT,
    registered_at         TEXT NOT NULL,
    extracted_at          TEXT,
    session_kind          TEXT DEFAULT 'user',
    last_completed_offset INTEGER,
    last_completed_at     TEXT,
    last_extracted_offset INTEGER,
    last_extracted_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_date ON sessions(date);
CREATE TABLE IF NOT EXISTS session_bindings (
    trowel_session_id TEXT PRIMARY KEY,
    cc_session_id     TEXT NOT NULL,
    session_kind      TEXT NOT NULL,
    workdir           TEXT NOT NULL,
    bound_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_bindings_cc ON session_bindings(cc_session_id);
"""

#: columns added by slice-040-b's first schema evolution. SQLite has no
#: ``ADD COLUMN IF NOT EXISTS`` — ``_ensure_columns`` reflects via PRAGMA and
#: adds only what's missing, so a pre-040-b db (6 columns) upgrades on connect.
_ADD_COLUMN_SQL = {
    "session_kind": "ALTER TABLE sessions ADD COLUMN session_kind TEXT DEFAULT 'user'",
    "last_completed_offset": "ALTER TABLE sessions ADD COLUMN last_completed_offset INTEGER",
    "last_completed_at": "ALTER TABLE sessions ADD COLUMN last_completed_at TEXT",
    "last_extracted_offset": "ALTER TABLE sessions ADD COLUMN last_extracted_offset INTEGER",
    "last_extracted_at": "ALTER TABLE sessions ADD COLUMN last_extracted_at TEXT",
}


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
    # slice-061: trowel's session id (CCHost ``session_id``). NOT persisted into
    # the sessions table — ``register()`` uses it to write the session_bindings
    # row so access/outcome records written before cc init (empty
    # cc_session_id) can be attributed back via trowel_session_id (C-3).
    # Defaults to "" for legacy callers; an empty value skips the bind.
    trowel_session_id: str = ""
    jsonl_path: str = ""
    registered_at: str = ""
    extracted_at: str | None = None
    session_kind: str = "user"
    last_completed_offset: int | None = None
    last_completed_at: str | None = None
    last_extracted_offset: int | None = None
    last_extracted_at: str | None = None


@dataclass(frozen=True)
class SessionBinding:
    """One persisted trowel→cc identity mapping (slice-061).

    The memory MCP records every retrieval under ``trowel_session_id`` (known
    at CCHost spawn, before cc init emits a cc_session_id). This binding lets
    judge/metrics resolve those records back to a cc session + kind AFTER init
    lands — instead of relying on a non-empty ``cc_session_id`` at write time
    (C-3: identity must not depend on init timing; that is why 842/845 access
    rows carried an empty cc_session_id).

    A cc session may be resumed from several trowel sessions, so the relation
    is many-to-one (multiple ``trowel_session_id`` → one ``cc_session_id``);
    ``trowel_session_id`` is the PK and a re-bind is a no-op (C-4: never
    overwrite a prior bind).

    Attributes:
        trowel_session_id: trowel's session id (CCHost ``session_id``; PK).
        cc_session_id: cc's uuid session id resolved at init.
        session_kind: user / review / distill / eval — carried on the binding
            so kind filtering never depends on a non-empty cc_session_id.
        workdir: the session's working directory.
        bound_at: ISO timestamp the binding was persisted.
    """

    trowel_session_id: str
    cc_session_id: str
    session_kind: str
    workdir: str
    bound_at: str


@runtime_checkable
class SessionRegistrar(Protocol):
    """The narrow registrar surface ``CCHost`` needs (slice-040-b).

    CCHost only registers a session at init and stamps the completed-offset
    water mark at the ``result`` turn boundary. The find / advance-extracted
    queries stay on the concrete ``SessionsRepository`` (a review_job concern,
    not CCHost's) and are deliberately OUT of this Protocol. Production wires
    the concrete ``SessionsRepository``; tests inject a no-op/capturing fake so
    they never touch the real ``~/.trowel/memory/meta/sessions.db``.

    ``when`` is optional on both methods — None means the implementation stamps
    ``datetime.now()`` itself, so CCHost (which has only a monotonic clock) does
    not have to fabricate a wall-clock timestamp.
    """

    def register(self, rec: SessionRecord) -> None: ...

    def update_completed(
        self, cc_session_id: str, completed_bytes: int, when: str | None = None
    ) -> None: ...


@dataclass(frozen=True)
class IncrementalSegment:
    """One not-yet-distilled slice of a session (slice-040-b C-6).

    The daily review reads ``(last_extracted_offset, last_completed_offset]`` per
    session; this is the repo's view of one such slice. ``end`` is the completed
    water mark (a byte offset of a fully-flushed turn), ``start`` is where the
    previous distillation left off (0 if never distilled). Resuming a session
    and finishing new turns pushes ``end`` forward; the next review distils only
    the new slice — so a session is never "sealed" by a one-shot ``extracted_at``
    (C-7).

    Attributes:
        session: the owning SessionRecord (cc_session_id, jsonl_path, workdir).
        start: byte offset the segment starts at (== last_extracted_offset or 0).
        end: byte offset the segment ends at (== last_completed_offset).
    """

    session: SessionRecord
    start: int
    end: int


class SessionsRepository:
    """CRUD over the sessions table. Holds one sqlite connection."""

    def __init__(self, conn: sqlite3.Connection, *, migrate: bool = True) -> None:
        self._conn = conn
        self._conn.row_factory = sqlite3.Row
        if migrate:
            self._conn.executescript(_CREATE_SQL)
            self._ensure_columns()
            self._conn.commit()
        # migrate=False: a read-only caller (slice-067 profile-recalibrate plan)
        # assumes a current-schema db and only SELECTs — skip the DDL so a ro
        # connection (mode=ro uri) is not asked to write. A stale-schema db
        # then surfaces as a clean query-time error instead of a silent write.

    def _ensure_columns(self) -> None:
        """Idempotent column additions (slice-040-b's first schema evolution).

        ``CREATE TABLE IF NOT EXISTS`` won't add columns to an existing table,
        so a pre-040-b db (6 columns) would otherwise crash the first register.
        Reflect via ``PRAGMA table_info`` and ``ALTER TABLE ADD COLUMN`` only
        what's missing. Safe to call on every connection.
        """
        existing = {
            row["name"] for row in self._conn.execute("PRAGMA table_info(sessions)")
        }
        for col, sql in _ADD_COLUMN_SQL.items():
            if col not in existing:
                self._conn.execute(sql)
        # the incremental index references the (possibly just-added) offset
        # columns, so it is created here — AFTER the columns exist — not in
        # _CREATE_SQL (which would crash on a pre-040-b db whose old table is
        # missing those columns: CREATE TABLE IF NOT EXISTS skips the existing
        # table but a referencing CREATE INDEX still fails on the missing cols).
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sessions_incremental"
            " ON sessions(last_completed_offset, last_extracted_offset)"
        )

    def register(self, rec: SessionRecord) -> None:
        """Idempotent insert (PK). Re-registering a session_id is a no-op.

        cc's session-start hook (``service.py``) may fire more than once for a
        session; the PK keeps this harmless. ``session_kind`` is stamped on the
        first registration (slice-040-b): review sessions pass ``"review"`` so
        ``find_pending(exclude_kinds=["review"])`` can keep them out of the
        distillation queue without guessing from the workdir path.
        """
        self._conn.execute(
            "INSERT OR IGNORE INTO sessions"
            " (cc_session_id, workdir, date, jsonl_path, registered_at,"
            " extracted_at, session_kind)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                rec.cc_session_id,
                rec.workdir,
                rec.date,
                rec.jsonl_path,
                rec.registered_at,
                rec.extracted_at,
                rec.session_kind,
            ),
        )
        # slice-061: persist the trowel→cc binding alongside the session row so
        # retrieval records written before cc init (empty cc_session_id) resolve
        # later via trowel_session_id (C-3). Idempotent on trowel_id (C-4).
        if rec.trowel_session_id:
            self.bind_session(
                SessionBinding(
                    trowel_session_id=rec.trowel_session_id,
                    cc_session_id=rec.cc_session_id,
                    session_kind=rec.session_kind,
                    workdir=rec.workdir,
                    bound_at=rec.registered_at or datetime.now().isoformat(),
                )
            )
        self._conn.commit()

    def find_pending(
        self,
        date: str,
        exclude_workdir_substr: str = "",
        exclude_kinds: list[str] | None = None,
    ) -> list[SessionRecord]:
        """Return sessions of ``date`` not yet extracted.

        Args:
            date: ISO ``YYYY-MM-DD``.
            exclude_workdir_substr: legacy escape hatch — if set, skip sessions
                whose workdir contains this substring (e.g.
                ``"review-daily-work"``). Kept for old rows registered before
                ``session_kind`` existed; new code prefers ``exclude_kinds``.
                Must NOT contain LIKE wildcards (``%`` / ``_``) — it is matched
                via SQL ``LIKE``; the sole caller passes a literal path segment.
            exclude_kinds: session kinds to exclude (slice-040-b C-5 — filter by
                kind, NOT by workdir path). ``"review"`` keeps the distillation
                sessions themselves out of the queue. NULL kinds (pre-040-b
                legacy rows) are treated as ``"user"`` and never excluded here.

        Returns:
            pending sessions, oldest first (stable extraction order).
        """
        clauses = ["date = ?", "extracted_at IS NULL"]
        params: list = [date]
        if exclude_workdir_substr:
            clauses.append("workdir NOT LIKE ?")
            params.append(f"%{exclude_workdir_substr}%")
        if exclude_kinds:
            ph = ",".join("?" * len(exclude_kinds))
            # COALESCE so a legacy NULL kind reads as 'user' and is kept when
            # only 'review' is excluded (C-5: never guess from the path).
            clauses.append(f"COALESCE(session_kind, 'user') NOT IN ({ph})")
            params.extend(exclude_kinds)
        sql = (
            "SELECT * FROM sessions WHERE "
            + " AND ".join(clauses)
            + " ORDER BY registered_at"
        )
        rows = self._conn.execute(sql, params).fetchall()
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

    def update_completed(
        self, cc_session_id: str, completed_bytes: int, when: str | None = None
    ) -> None:
        """Stamp the completed water mark (slice-040-b C-6).

        Called by CCHost at the ``result`` turn boundary with the jsonl byte size
        — every turn up to ``completed_bytes`` is fully flushed and safe to
        distill. Half-turns (no result yet) never call this, so they stay out of
        ``find_incremental``.
        """
        stamp = when or datetime.now().isoformat()
        self._conn.execute(
            "UPDATE sessions SET last_completed_offset = ?, last_completed_at = ?"
            " WHERE cc_session_id = ?",
            (completed_bytes, stamp, cc_session_id),
        )
        self._conn.commit()

    def find_incremental(self) -> list[IncrementalSegment]:
        """Return every session with a not-yet-distilled completed slice (C-6/C-7).

        A session is included iff it has a completed water mark strictly greater
        than its extracted water mark (NULL extracted → 0). ONLY user sessions
        are eligible (slice-053): review / distill / eval agent sessions are all
        excluded by kind (C-5: by kind, not workdir). A review that distilled a
        distill/eval agent's own run would both pollute notes with agent chatter
        AND recurse (judging a judge). Legacy NULL kinds read as ``'user'`` via
        COALESCE so pre-040-b rows are still picked up.
        """
        rows = self._conn.execute(
            "SELECT * FROM sessions"
            " WHERE COALESCE(session_kind, 'user') = 'user'"
            " AND last_completed_offset IS NOT NULL"
            " AND last_completed_offset > COALESCE(last_extracted_offset, 0)"
            " ORDER BY registered_at"
        ).fetchall()
        segments: list[IncrementalSegment] = []
        for row in rows:
            rec = _row_to_record(row)
            # SQL already guarantees end IS NOT NULL and end > COALESCE(start, 0).
            # Mirror that here with explicit None checks (the `or 0` form would
            # conflate an explicit 0 with NULL — harmless, but less clear).
            start = (
                rec.last_extracted_offset
                if rec.last_extracted_offset is not None
                else 0
            )
            end = rec.last_completed_offset or 0
            if end > start:
                segments.append(IncrementalSegment(session=rec, start=start, end=end))
        return segments

    def advance_extracted(
        self, cc_session_id: str, end_offset: int, when: str | None = None
    ) -> None:
        """Push the extracted water mark forward after a slice is persisted (C-7).

        ``end_offset`` is the completed offset of the slice just distilled; the
        next ``find_incremental`` will only surface work beyond it. Idempotent in
        the sense that re-advancing to the same offset is a harmless overwrite.
        """
        stamp = when or datetime.now().isoformat()
        self._conn.execute(
            "UPDATE sessions SET last_extracted_offset = ?, last_extracted_at = ?"
            " WHERE cc_session_id = ?",
            (end_offset, stamp, cc_session_id),
        )
        self._conn.commit()

    def find_all_completed_sessions(
        self, exclude_kinds: list[str] | None = None
    ) -> list[SessionRecord]:
        """Return every session with a completed offset, regardless of extracted
        state (slice-050: the profile distill job keeps its OWN watermark in
        ``meta/profile-distill-state.json``, so review's
        ``last_extracted_offset`` is irrelevant here — C-7).

        By default review AND distill kind sessions are excluded: the distill
        job must never re-distill its own agent runs, nor the review agent's
        (C-5: filter by kind, not workdir path). Pass ``exclude_kinds`` to
        override.

        Args:
            exclude_kinds: session kinds to drop (default
                ``["review", "distill"]``). Legacy NULL kinds read as ``'user'``
                via COALESCE so pre-040-b rows are still picked up.

        Returns:
            candidate sessions, oldest first (stable processing order). The
            distill job subtracts those already in profile-distill-state.json
            to find the real backlog.
        """
        excluded = exclude_kinds if exclude_kinds is not None else ["review", "distill"]
        ph = ",".join("?" * len(excluded))
        rows = self._conn.execute(
            "SELECT * FROM sessions"
            " WHERE COALESCE(session_kind, 'user') NOT IN ({ph})"
            " AND last_completed_offset IS NOT NULL"
            " ORDER BY registered_at".format(ph=ph),
            excluded,
        ).fetchall()
        return [_row_to_record(r) for r in rows]

    # ---- slice-061: persistent session binding (trowel_id -> cc_id + kind) ----

    def bind_session(self, binding: SessionBinding) -> None:
        """Persist a trowel→cc identity mapping (idempotent on trowel_id).

        ``INSERT OR IGNORE`` on the ``trowel_session_id`` PK: a re-bind (init
        firing twice, or a registrar replay) is a no-op that NEVER overwrites a
        prior bind (C-4). A cc session resumed from a second trowel session
        adds a second row — many trowel ids may map to one cc id.
        """
        self._conn.execute(
            "INSERT OR IGNORE INTO session_bindings"
            " (trowel_session_id, cc_session_id, session_kind, workdir, bound_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (
                binding.trowel_session_id,
                binding.cc_session_id,
                binding.session_kind,
                binding.workdir,
                binding.bound_at,
            ),
        )
        self._conn.commit()

    def find_cc_by_trowel(
        self, trowel_session_id: str
    ) -> SessionBinding | None:
        """Resolve one trowel id to its cc binding, or None if never bound (C-3).

        This is the primary attribution key: an access/outcome record carrying a
        ``trowel_session_id`` resolves to a cc session + kind through here, even
        when the record's own ``cc_session_id`` was empty (written before init).
        """
        row = self._conn.execute(
            "SELECT * FROM session_bindings WHERE trowel_session_id = ?",
            (trowel_session_id,),
        ).fetchone()
        return _row_to_binding(row) if row is not None else None

    def find_trowels_by_cc(self, cc_session_id: str) -> list[SessionBinding]:
        """Return every trowel id bound to this cc id, oldest bind first (C-4).

        judge/metrics use this to gather ALL retrieval records of one cc session
        — including those written under a different trowel id before/after a
        resume — instead of filtering access-log by a single cc_session_id.
        """
        rows = self._conn.execute(
            "SELECT * FROM session_bindings WHERE cc_session_id = ?"
            " ORDER BY bound_at",
            (cc_session_id,),
        ).fetchall()
        return [_row_to_binding(r) for r in rows]

    def all_bindings(self) -> list[SessionBinding]:
        """Return every persisted binding (slice-061 attribution index source).

        judge/metrics build an in-memory ``trowel_id → binding`` index from this
        once per run instead of hitting the db per access/outcome record.
        """
        rows = self._conn.execute(
            "SELECT * FROM session_bindings"
        ).fetchall()
        return [_row_to_binding(r) for r in rows]

    def all_cc_kinds(self) -> dict[str, str]:
        """Return ``{cc_session_id: session_kind}`` for every registered session.

        The cc_session_id fallback path of attribution needs a kind for a record
        that carries a non-empty cc_session_id but no trowel binding. NULL kinds
        (pre-040-b legacy rows) read as ``'user'`` via COALESCE.
        """
        rows = self._conn.execute(
            "SELECT cc_session_id, COALESCE(session_kind, 'user') FROM sessions"
        ).fetchall()
        return {row[0]: row[1] for row in rows if row[0]}


def create_sessions_repository(
    conn: sqlite3.Connection, *, migrate: bool = True
) -> SessionsRepository:
    """Factory mirroring the cards/review repository pattern.

    ``migrate=False`` skips the schema DDL so a read-only connection (e.g. the
    slice-067 recalibration ``plan``) can SELECT without being asked to write.
    """
    return SessionsRepository(conn, migrate=migrate)


def open_sessions_db(memory_root: Path) -> sqlite3.Connection:
    """Open (creating the meta dir if needed) sessions.db under memory_root."""
    meta = memory_root / _META_DIR
    meta.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(meta / _SESSIONS_DB))
    conn.row_factory = sqlite3.Row
    return conn


def open_sessions_db_readonly(
    memory_root: Path,
) -> sqlite3.Connection | None:
    """Open sessions.db read-only; return None if it does not exist.

    slice-067: the recalibration ``plan`` must never materialize a sessions.db
    on a fresh root or migrate a stale one (§4 plan is read-only). Unlike
    ``open_sessions_db`` this does NOT create the file or the meta dir, and the
    ``mode=ro`` uri refuses to write even if a caller later tries. Returns None
    for a missing db so the caller can treat it as "no sessions registered yet".
    """
    db = memory_root / _META_DIR / _SESSIONS_DB
    if not db.exists():
        return None
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
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
        session_kind=row["session_kind"] or "user",
        last_completed_offset=row["last_completed_offset"],
        last_completed_at=row["last_completed_at"],
        last_extracted_offset=row["last_extracted_offset"],
        last_extracted_at=row["last_extracted_at"],
    )


def _row_to_binding(row: sqlite3.Row) -> SessionBinding:
    return SessionBinding(
        trowel_session_id=row["trowel_session_id"],
        cc_session_id=row["cc_session_id"],
        session_kind=row["session_kind"],
        workdir=row["workdir"],
        bound_at=row["bound_at"],
    )
