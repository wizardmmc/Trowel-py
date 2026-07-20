"""access-log / outcome-log for the online read path (slice-040-c).

Append-only JSONL of retrieval actions (search/read) and model feedback
(outcome). The logs are the source of truth for north-star metrics
(``pre_failure_recall``, ``memory_helpfulness``); Note.refs/helpful/harmful
are rebuildable caches from these.

C-9: per-call identity uses ``toolUseId`` (cc's ``_meta.claudecode/toolUseId``,
reverse-verified to reach the server) + ``trowel_session_id`` + ``ts``.
``turn_id``/``generation`` are NOT carried — cc does not send them.

slice-078: identity is host-neutral. ``host_kind`` (``cc`` / ``codex``) +
``native_session_id`` (cc_session_id / Codex thread_id) are the new canonical
pair; ``cc_session_id`` is kept for back-compat reads of pre-078 logs (it
mirrors ``native_session_id`` on the CC path). Codex has no per-call toolUseId
(MCP carries only name+arguments), so ``toolUseId`` is empty there and
uniqueness falls back to ``trowel_session_id + native_session_id + ts +
server-generated search_id/read_id``.

Append uses O_APPEND (single-line write); one corrupt line is skipped so it
never makes the whole history unreadable (038 W1). Multiple per-session MCP
servers append concurrently; single-line JSON writes under PIPE_BUF are atomic
on POSIX, which suffices at the current scale.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

_META_DIR = "meta"
_ACCESS_LOG = "access-log.jsonl"
_OUTCOME_LOG = "outcome-log.jsonl"

Action = Literal["search", "read"]
Outcome = Literal["helpful", "harmful", "unused", "unknown"]


@dataclass(frozen=True)
class AccessRecord:
    """One retrieval action (a search returning candidates, or a read opening a body).

    Attributes:
        ts: ISO timestamp of the action.
        trowel_session_id: trowel's session id (injected via env).
        cc_session_id: cc's session id (empty for fresh sessions until init).
            Kept for back-compat reads of pre-078 logs; on new writes it
            mirrors ``native_session_id`` on the CC path (and is empty on the
            Codex path, which has no cc session).
        toolUseId: cc's per-call tool-use id (from _meta.claudecode/toolUseId).
            Empty on the Codex path — MCP carries only name+arguments, so the
            server cannot recover a per-call id there.
        action: "search" (candidates returned) or "read" (body opened, retrieved).
        search_id: server-generated id for this search (read records carry the
            originating search_id to link candidate→open).
        read_id: server-generated id for this read (empty for search records).
        query: the search query (empty for read records).
        memory_id: the note id (empty for search records; for read, the opened note).
        rank: rank of this candidate in the search result (None for read records).
        host_kind: slice-078 host-neutral identity — ``cc`` or ``codex``. Empty
            on pre-078 log lines (back-compat).
        native_session_id: slice-078 host-neutral identity — the native host's
            session id (cc_session_id on CC, thread_id on Codex). Empty on
            pre-078 log lines.
    """

    ts: str
    trowel_session_id: str
    cc_session_id: str
    toolUseId: str
    action: Action
    search_id: str
    read_id: str = ""
    query: str = ""
    memory_id: str = ""
    rank: int | None = None
    host_kind: str = ""
    native_session_id: str = ""


@dataclass(frozen=True)
class OutcomeRecord:
    """Model feedback after reading a note (C-6: helpful/harmful both recorded).

    The ``host_kind`` / ``native_session_id`` fields mirror
    :class:`AccessRecord` (slice-078 host-neutral identity); both default to
    empty for back-compat with pre-078 outcome logs.
    """

    ts: str
    trowel_session_id: str
    cc_session_id: str
    toolUseId: str
    read_id: str
    memory_id: str
    outcome: Outcome
    reason: str = ""
    host_kind: str = ""
    native_session_id: str = ""


def log_access(root: Path | str, rec: AccessRecord) -> None:
    """Append one access record to ``meta/access-log.jsonl``."""
    _append(Path(root) / _META_DIR / _ACCESS_LOG, asdict(rec))


def log_outcome(root: Path | str, rec: OutcomeRecord) -> None:
    """Append one outcome record to ``meta/outcome-log.jsonl``."""
    _append(Path(root) / _META_DIR / _OUTCOME_LOG, asdict(rec))


def read_access_log(root: Path | str) -> list[AccessRecord]:
    """Return all access records in append order (empty list if absent)."""
    return _read(Path(root) / _META_DIR / _ACCESS_LOG, AccessRecord)


def read_outcome_log(root: Path | str) -> list[OutcomeRecord]:
    """Return all outcome records in append order (empty list if absent)."""
    return _read(Path(root) / _META_DIR / _OUTCOME_LOG, OutcomeRecord)


def _append(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def _read(path: Path, cls: type) -> list:
    """Read all records in append order, skipping corrupt lines (038 W1)."""
    if not path.exists():
        return []
    out: list = []
    for i, raw in enumerate(path.read_text(encoding="utf-8").splitlines()):
        line = raw.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            logger.warning("skipping corrupt %s line %d: %r", path.name, i, line[:80])
            continue
        try:
            out.append(cls(**obj))
        except TypeError:
            logger.warning("skipping malformed %s line %d (missing keys)", path.name, i)
    return out
