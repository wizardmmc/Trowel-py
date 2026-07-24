"""memory MCP 的搜索、读取与反馈处理器。"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from trowel_py.memory.access_log import (
    AccessRecord,
    Outcome,
    OutcomeRecord,
    log_access,
    log_outcome,
    read_access_log,
)
from trowel_py.memory.store import MemoryStore
from trowel_py.memory.types import Note

_TOOL_SEARCH = "search"
_TOOL_READ = "read"
_TOOL_OUTCOME = "outcome"
_DICT_L0 = "dictionary-L0.md"
_REQUIRES_READ_KINDS = {"gotcha", "procedure", "hypothesis"}
_URI_PREFIX = "memory://notes/"


def parse_memory_uri(uri: str) -> str:
    """校验 memory note URI 并返回安全的 note id。"""
    if not uri.startswith(_URI_PREFIX):
        raise ValueError(f"not a memory URI: {uri!r}")
    note_id = uri[len(_URI_PREFIX) :]
    if not note_id or "/" in note_id or ".." in note_id or note_id.startswith("."):
        raise ValueError(f"illegal note id: {note_id!r}")
    return note_id


def requires_read(note: Note) -> bool:
    """判断候选是否必须读取正文后才能使用。"""
    return note.kind in _REQUIRES_READ_KINDS or note.verification == "inferred-untested"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _hit(
    note_id: str,
    note: Note,
    rank: int,
    *,
    requires_read_fn: Any = requires_read,
) -> dict[str, Any]:
    return {
        "memory_id": note_id,
        "title": note.title,
        "summary": note.summary,
        "uri": f"memory://notes/{note_id}",
        "score": 1.0 / (rank + 1),
        "kind": note.kind,
        "verification": note.verification,
        "status": note.status,
        "requires_read": requires_read_fn(note),
    }


def handle_search(
    query: str,
    top_k: int,
    include_inactive: bool,
    store: MemoryStore,
    dictionary_path: Path,
    identity: dict[str, str],
    toolUseId: str = "",
    retriever: Any = None,
    *,
    now_fn: Any = _now,
    hit_fn: Any = _hit,
    log_access_fn: Any = log_access,
) -> dict[str, Any]:
    """通过两层字典检索 notes，并记录搜索访问。"""
    search_id = uuid.uuid4().hex
    log_access_fn(
        store.root,
        AccessRecord(
            ts=now_fn(),
            trowel_session_id=identity["trowel_session_id"],
            cc_session_id=identity["cc_session_id"],
            host_kind=identity["host_kind"],
            native_session_id=identity["native_session_id"],
            toolUseId=toolUseId,
            action="search",
            search_id=search_id,
            query=query,
        ),
    )
    if not dictionary_path.exists():
        return {
            "search_id": search_id,
            "results": [],
            "error": "dictionary_empty",
            "hint": "run: trowel memory dict-rebuild --apply",
        }
    if retriever is None:
        from trowel_py.config import load_llm_config
        from trowel_py.llm.client import AnthropicProvider
        from trowel_py.memory.retrievers import LLMRetriever

        retriever = LLMRetriever(AnthropicProvider(load_llm_config()))

    from trowel_py.memory.dictionary_lock import dictionary_lock
    from trowel_py.memory.dictionary_state import load_state

    # 锁覆盖索引、状态与检索器调用，确保提示描述的是同一代字典。
    with dictionary_lock(store.root, exclusive=False):
        stale_warning = (
            "dictionary index is stale; results may be incomplete. "
            "run: trowel memory dict-rebuild --apply"
            if load_state(store.root).status != "consistent"
            else None
        )
        stems = retriever(
            query,
            corpus_dir=str(store.root / "notes"),
            dictionary_path=dictionary_path,
        )

    hits: list[dict[str, Any]] = []
    for rank, stem in enumerate(stems[:top_k]):
        note = store.load_note(stem)
        if note is None:
            continue
        if not include_inactive and note.status != "active":
            continue
        hits.append(hit_fn(stem, note, rank))
        log_access_fn(
            store.root,
            AccessRecord(
                ts=now_fn(),
                trowel_session_id=identity["trowel_session_id"],
                cc_session_id=identity["cc_session_id"],
                host_kind=identity["host_kind"],
                native_session_id=identity["native_session_id"],
                toolUseId=toolUseId,
                action="search",
                search_id=search_id,
                memory_id=stem,
                rank=rank,
            ),
        )

    result: dict[str, Any] = {"search_id": search_id, "results": hits}
    if stale_warning:
        result["warning"] = stale_warning
    return result


def handle_read(
    uri: str,
    search_id: str,
    store: MemoryStore,
    identity: dict[str, str],
    toolUseId: str = "",
    *,
    parse_uri_fn: Any = parse_memory_uri,
    now_fn: Any = _now,
    today_fn: Any = _today,
    log_access_fn: Any = log_access,
) -> dict[str, Any]:
    """读取 note 正文，并更新引用和访问日志。"""
    try:
        note_id = parse_uri_fn(uri)
    except ValueError as exc:
        return {"error": str(exc)}
    note = store.load_note(note_id)
    if note is None:
        return {"error": "not_found", "uri": uri}
    store.record_ref(note_id, today_fn())
    read_id = uuid.uuid4().hex
    log_access_fn(
        store.root,
        AccessRecord(
            ts=now_fn(),
            trowel_session_id=identity["trowel_session_id"],
            cc_session_id=identity["cc_session_id"],
            host_kind=identity["host_kind"],
            native_session_id=identity["native_session_id"],
            toolUseId=toolUseId,
            action="read",
            search_id=search_id,
            read_id=read_id,
            memory_id=note_id,
        ),
    )
    return {
        "read_id": read_id,
        "title": note.title,
        "body": note.body,
        "kind": note.kind,
        "verification": note.verification,
        "status": note.status,
        "tags": list(note.tags),
    }


def handle_outcome(
    read_id: str,
    outcome: str,
    reason: str,
    root: Path,
    identity: dict[str, str],
    toolUseId: str = "",
    *,
    now_fn: Any = _now,
    read_access_log_fn: Any = read_access_log,
    log_outcome_fn: Any = log_outcome,
) -> dict[str, Any]:
    """记录已读取 note 的模型反馈。"""
    if outcome not in ("helpful", "harmful", "unused", "unknown"):
        return {"error": f"invalid outcome: {outcome!r}"}
    records = read_access_log_fn(root)
    match = next(
        (r for r in records if r.read_id == read_id and r.action == "read"), None
    )
    if match is None:
        return {"error": "unknown_read_id", "read_id": read_id}
    log_outcome_fn(
        root,
        OutcomeRecord(
            ts=now_fn(),
            trowel_session_id=identity["trowel_session_id"],
            cc_session_id=identity["cc_session_id"],
            host_kind=identity["host_kind"],
            native_session_id=identity["native_session_id"],
            toolUseId=toolUseId,
            read_id=read_id,
            memory_id=match.memory_id,
            outcome=cast(Outcome, outcome),
            reason=reason,
        ),
    )
    return {"ok": True, "read_id": read_id, "outcome": outcome}
