"""为 judge 整理会话访问记录和 Memory 索引上下文。"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from pathlib import Path

from trowel_py.memory.access_log import AccessRecord, read_access_log
from trowel_py.memory.attribution import AttributionIndex
from trowel_py.memory.store import MemoryStore


def _summarize_access_log(
    root: Path,
    cc_session_id: str,
    index: AttributionIndex,
    *,
    read_access_log_fn: Callable[[Path | str], list[AccessRecord]] = read_access_log,
) -> str:
    """汇总归属于指定 CC 会话的 Search 和 Read 记录。

    归属由 ``AttributionIndex`` 解析，而非只信任日志自带的 CC 会话 ID。
    Search 按 ``search_id`` 首次出现的顺序分组，查询取该组首条记录的值。
    Search 候选中的 ``memory_id`` 是 Note 文件 stem，不是 ``Note.memory_id``；
    空值过滤、重复值合并后按字典序排列。Read 保持日志顺序；计数包含空
    ``memory_id`` 的 Read，展示列表则过滤空值。其他会话的记录不会进入结果，
    未知 ``action`` 也会被忽略。

    Args:
        root: 访问日志所在的 Memory 根目录。
        cc_session_id: 只保留归属于该 CC 会话的记录。
        index: 解析 Trowel 绑定和 CC 会话归属的内存索引。
        read_access_log_fn: 访问日志读取入口；judge 门面通过该参数传入其可替换
            的 ``read_access_log``。

    Returns:
        供判效提示词使用的文本。没有归属记录时返回明确的无检索说明；有归属
        记录但全是未知 ``action`` 时返回空字符串。
    """
    records = [
        record
        for record in read_access_log_fn(root)
        if index.resolve(
            record.trowel_session_id,
            record.cc_session_id,
            host_kind=record.host_kind,
            native_session_id=record.native_session_id,
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
    """读取 L0 Dictionary，空缺时用现有 Note 生成最小索引。

    非空 L0 会去除首尾空白后原样返回。回退索引使用持久化的
    ``Note.memory_id``，不是 Note 文件 stem；只保留 ID 非空的 Note，并按
    Store 返回顺序输出 ID 与摘要，不排序或去重。没有可用 Note 时返回明确的
    空库说明。

    Args:
        store: 提供 L0 和现有 Note 的 Memory Store。

    Returns:
        L0 正文、最小 Note 索引或空库说明。
    """
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
