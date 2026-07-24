"""judge 使用的检索证据与字典上下文。"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from trowel_py.memory.access_log import AccessRecord, read_access_log
from trowel_py.memory.attribution import AttributionIndex
from trowel_py.memory.store import MemoryStore


def _summarize_access_log(
    root: Path,
    cc_session_id: str,
    index: AttributionIndex,
    *,
    read_access_log_fn=read_access_log,
) -> str:
    records = [
        record
        for record in read_access_log_fn(root)
        if index.resolve(
            record.trowel_session_id,
            record.cc_session_id,
        ).cc_session_id
        == cc_session_id
    ]
    if not records:
        return "（该会话没有检索记录：没 search 也没 read）"

    by_search: dict[str, list[AccessRecord]] = defaultdict(list)
    queries: dict[str, str] = {}
    reads: list[AccessRecord] = []
    for record in records:
        if record.action == "search":
            by_search[record.search_id].append(record)
            queries.setdefault(record.search_id, record.query)
        elif record.action == "read":
            reads.append(record)

    lines: list[str] = []
    if by_search:
        lines.append("search:")
        for search_id, candidates in by_search.items():
            query = queries.get(search_id, "")
            memory_ids = sorted(
                {candidate.memory_id for candidate in candidates if candidate.memory_id}
            )
            candidate_text = ", ".join(memory_ids) if memory_ids else "(无候选)"
            lines.append(f"  - query={query!r} 候选=[{candidate_text}]")
    if reads:
        read_ids = [record.memory_id for record in reads if record.memory_id]
        lines.append(f"read: {len(reads)} 条 -> {read_ids}")
    return "\n".join(lines)


def _dictionary_index(store: MemoryStore) -> str:
    index = store.load_dictionary_L0().strip()
    if index:
        return index
    rows = [
        (note.memory_id, note.summary)
        for _stem, note in store.load_notes_with_id()
        if note.memory_id
    ]
    if not rows:
        return "（暂无笔记）"
    return "\n".join(f"- {memory_id}: {summary}" for memory_id, summary in rows)
