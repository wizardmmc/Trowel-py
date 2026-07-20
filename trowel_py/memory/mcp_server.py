"""memory MCP server (slice-040-c): search/read/outcome over the memory tree.

Per-session stdio subprocess spawned by cc via ``--mcp-config``. Identity
(trowel/cc_session_id/MEMORY_ROOT) is read from env (injected by CCHost);
per-call toolUseId from ``_meta.claudecode/toolUseId`` (reverse-verified,
spike 2026-07-11).

Uses the lowlevel ``Server`` with ``request_handlers[CallToolRequest]``
overridden — the ``@server.call_tool()`` convenience decorator drops
``_meta`` (only passes name/arguments). See docs/design/memory/mcp-read-path-spike.md.

The pure-logic handlers (``handle_search`` / ``handle_read`` / ``handle_outcome``)
take identity + toolUseId as parameters so they are unit-testable without env.
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import mcp.types as types
from mcp.server import NotificationOptions, Server
from mcp.server.stdio import stdio_server

from trowel_py.memory.access_log import (
    AccessRecord,
    OutcomeRecord,
    log_access,
    log_outcome,
    read_access_log,
)
from trowel_py.memory.store import MemoryStore
from trowel_py.memory.types import Note

logger = logging.getLogger(__name__)

_TOOL_SEARCH = "search"
_TOOL_READ = "read"
_TOOL_OUTCOME = "outcome"
_DICT_L0 = "dictionary-L0.md"
_REQUIRES_READ_KINDS = {"gotcha", "procedure", "hypothesis"}
_URI_PREFIX = "memory://notes/"


def parse_memory_uri(uri: str) -> str:
    """Validate a ``memory://notes/<id>`` URI and return the note id (C-7).

    Raises ValueError on non-memory URI, absolute paths, ``..``, leading dot,
    or embedded slash (symlink/path-traversal escape).
    """
    if not uri.startswith(_URI_PREFIX):
        raise ValueError(f"not a memory URI: {uri!r}")
    note_id = uri[len(_URI_PREFIX):]
    if not note_id or "/" in note_id or ".." in note_id or note_id.startswith("."):
        raise ValueError(f"illegal note id: {note_id!r}")
    return note_id


def requires_read(note: Note) -> bool:
    """Candidate-level requires_read (C-4): gotcha/procedure/hypothesis or inferred-untested."""
    return note.kind in _REQUIRES_READ_KINDS or note.verification == "inferred-untested"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _identity_from_env() -> dict[str, str]:
    """Read host-neutral identity from env (slice-078).

    The canonical pair is ``TROWEL_HOST_KIND`` (``cc`` / ``codex``) +
    ``TROWEL_NATIVE_SESSION_ID`` (cc_session_id / Codex thread_id). The legacy
    ``CC_SESSION_ID`` env (set by the CC host pre-078) is still read so a CC
    spawn that has not been updated to set the new vars keeps working: when
    ``host_kind`` is empty but ``CC_SESSION_ID`` is set, we back-fill
    ``host_kind="cc"`` and ``native_session_id=cc_session_id``.
    """
    cc_session_id = os.environ.get("CC_SESSION_ID", "")
    host_kind = os.environ.get("TROWEL_HOST_KIND", "")
    native_session_id = os.environ.get("TROWEL_NATIVE_SESSION_ID", "")
    if not host_kind and cc_session_id:
        # Back-compat: a CC spawn predating slice-078 sets only CC_SESSION_ID.
        host_kind = "cc"
        native_session_id = native_session_id or cc_session_id
    return {
        "trowel_session_id": os.environ.get("TROWEL_SESSION_ID", ""),
        "cc_session_id": cc_session_id,
        "host_kind": host_kind,
        "native_session_id": native_session_id,
    }


def _tooluse_id(meta: object) -> str:
    if meta is None:
        return ""
    dump = meta.model_dump(exclude_none=True) if hasattr(meta, "model_dump") else dict(meta)
    return str(dump.get("claudecode/toolUseId", ""))


def _hit(note_id: str, note: Note, rank: int) -> dict[str, Any]:
    """Build one search result entry from a note."""
    return {
        "memory_id": note_id,
        "title": note.title,
        "summary": note.summary,
        "uri": f"memory://notes/{note_id}",
        "score": 1.0 / (rank + 1),
        "kind": note.kind,
        "verification": note.verification,
        # slice-041: real status enum (was retired-bool mirror in 040-c).
        "status": note.status,
        "requires_read": requires_read(note),
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
) -> dict[str, Any]:
    """Search notes via the two-layer dictionary (L0→L1, S1 protocol).

    If the dictionary is absent, returns an empty result with a hint (does NOT
    lazy-build — search must not block on clustering, C-3).
    """
    search_id = uuid.uuid4().hex
    log_access(
        store.root,
        AccessRecord(
            ts=_now(),
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
    # slice-064 C-2/F11: take a shared dictionary lock around the L0/L1 read
    # AND the state read, so a concurrent rebuild cannot delete/swap an L1 file
    # mid-retrieval (two LLM round-trips happen inside the retriever) and the
    # stale warning describes the generation actually being served. The notes/
    # read below is off the index and needs no lock. Search never rebuilds (C-8).
    from trowel_py.memory.dictionary_lock import dictionary_lock
    from trowel_py.memory.dictionary_state import load_state

    with dictionary_lock(store.root, exclusive=False):
        # warn on ANY non-consistent state (stale / missing / corrupt-loaded-as
        # -missing), not just stale — slice-064 F6/codex: drift must be visible.
        stale_warning = (
            "dictionary index is stale; results may be incomplete. "
            "run: trowel memory dict-rebuild --apply"
            if load_state(store.root).status != "consistent" else None
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
        hits.append(_hit(stem, note, rank))
        log_access(
            store.root,
            AccessRecord(
                ts=_now(),
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
    out: dict[str, Any] = {"search_id": search_id, "results": hits}
    if stale_warning:
        out["warning"] = stale_warning
    return out


def handle_read(
    uri: str,
    search_id: str,
    store: MemoryStore,
    identity: dict[str, str],
    toolUseId: str = "",
) -> dict[str, Any]:
    """Read a note body by URI (C-7 closed). Side effect: record_ref + access-log."""
    try:
        note_id = parse_memory_uri(uri)
    except ValueError as exc:
        return {"error": str(exc)}
    note = store.load_note(note_id)
    if note is None:
        return {"error": "not_found", "uri": uri}
    store.record_ref(note_id, _today())
    read_id = uuid.uuid4().hex
    log_access(
        store.root,
        AccessRecord(
            ts=_now(),
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
        # slice-041: real status enum (was retired-bool mirror in 040-c).
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
) -> dict[str, Any]:
    """Record model feedback for a read (C-6). read_id must exist in access-log."""
    if outcome not in ("helpful", "harmful", "unused", "unknown"):
        return {"error": f"invalid outcome: {outcome!r}"}
    records = read_access_log(root)
    match = next((r for r in records if r.read_id == read_id and r.action == "read"), None)
    if match is None:
        return {"error": "unknown_read_id", "read_id": read_id}
    log_outcome(
        root,
        OutcomeRecord(
            ts=_now(),
            trowel_session_id=identity["trowel_session_id"],
            cc_session_id=identity["cc_session_id"],
            host_kind=identity["host_kind"],
            native_session_id=identity["native_session_id"],
            toolUseId=toolUseId,
            read_id=read_id,
            memory_id=match.memory_id,
            outcome=outcome,
            reason=reason,
        ),
    )
    return {"ok": True, "read_id": read_id, "outcome": outcome}


# --------------------------------------------------------------------------- MCP run

#: slice-052 温和版: search tool description nudges the model to memory.read
#: requires_read=true hits instead of summary-scanning them. The signal was
#: always returned (``_hit``), just never emphasized — audit found 304 search
#: / 13 read. The aggressive variant (drop summary when requires_read=true) is
#: a slice-053 spike; this keeps the search contract intact (C-6).
_SEARCH_DESC = (
    "Search memory notes by query. Returns candidates (title+summary+uri). "
    "Hits with requires_read=true must be opened with memory.read — don't "
    "rely on the summary alone."
)


def _build_server(root: Path) -> Server:
    """Build the lowlevel Server with the three memory tools."""
    server = Server("memory")
    store = MemoryStore(root)
    dictionary_path = root / _DICT_L0

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name=_TOOL_SEARCH,
                description=_SEARCH_DESC,
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "top_k": {"type": "integer", "default": 5},
                        "include_inactive": {"type": "boolean", "default": False},
                    },
                    "required": ["query"],
                },
            ),
            types.Tool(
                name=_TOOL_READ,
                description="Read a memory note body by uri (memory://notes/<id>).",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "uri": {"type": "string"},
                        "search_id": {"type": "string"},
                    },
                    "required": ["uri"],
                },
            ),
            types.Tool(
                name=_TOOL_OUTCOME,
                description="Feedback after reading: was the note helpful/harmful/unused?",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "read_id": {"type": "string"},
                        "outcome": {"type": "string", "enum": ["helpful", "harmful", "unused", "unknown"]},
                        "reason": {"type": "string"},
                    },
                    "required": ["read_id", "outcome"],
                },
            ),
        ]

    async def _handle_call_tool(req: types.CallToolRequest) -> types.ServerResult:
        params = req.params
        name = params.name
        args = params.arguments or {}
        identity = _identity_from_env()
        tool_use_id = _tooluse_id(params.meta)
        try:
            if name == _TOOL_SEARCH:
                result = handle_search(
                    query=args.get("query", ""),
                    top_k=int(args.get("top_k", 5)),
                    include_inactive=bool(args.get("include_inactive", False)),
                    store=store,
                    dictionary_path=dictionary_path,
                    identity=identity,
                    toolUseId=tool_use_id,
                )
            elif name == _TOOL_READ:
                result = handle_read(
                    uri=args.get("uri", ""),
                    search_id=args.get("search_id", ""),
                    store=store,
                    identity=identity,
                    toolUseId=tool_use_id,
                )
            elif name == _TOOL_OUTCOME:
                result = handle_outcome(
                    read_id=args.get("read_id", ""),
                    outcome=args.get("outcome", "unknown"),
                    reason=args.get("reason", ""),
                    root=root,
                    identity=identity,
                    toolUseId=tool_use_id,
                )
            else:
                result = {"error": f"unknown tool: {name}"}
        except Exception as exc:
            logger.exception("memory tool %s failed", name)
            result = {"error": f"internal error: {exc!r}"}
        is_error = isinstance(result, dict) and "error" in result
        return types.ServerResult(
            types.CallToolResult(
                content=[types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False))],
                isError=is_error,
            )
        )

    # bypass @server.call_tool() (drops _meta); register directly
    server.request_handlers[types.CallToolRequest] = _handle_call_tool
    return server


async def main() -> None:
    """Run the stdio MCP server. MEMORY_ROOT env selects the memory tree."""
    root_env = os.environ.get("MEMORY_ROOT", "").strip()
    if root_env:
        root = Path(root_env).expanduser()
    else:
        from trowel_py.memory.paths import resolve_memory_root

        root = resolve_memory_root()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    server = _build_server(root)
    init_options = server.create_initialization_options(
        notification_options=NotificationOptions(),
        experimental_capabilities={},
    )
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, init_options)


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
