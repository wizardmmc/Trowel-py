"""把 reflection 与 escalation 保存为带来源信息的会话级 Markdown。"""

from typing import Any

from trowel_py.memory.store import MemoryStore, _dump_frontmatter
from trowel_py.memory.provenance import completed_segment_to_dict, derivation_to_dict
from trowel_py.memory.types import PersistContext


def _write_meta(
    store: MemoryStore,
    rel_dir: str,
    meta_type: str,
    context: PersistContext,
    body: str,
) -> bool:
    """写入一项会话级 reflection 或 escalation。

    ``body`` 去掉首尾空白后若为空，则返回 ``False``，且不创建、覆盖或删除
    已有文件。正文非空时，去掉首尾空白并补一个末尾换行，然后写入
    ``<store.root>/<rel_dir>/<cc_session_id>.md``，覆盖同路径文件。
    frontmatter 固定记录 ``type``、``cc_session_id``、``segment_id`` 和
    ``review_date``；上下文提供 ``completed_segment`` 或 ``derivation`` 时，
    再分别记录 ``source`` 或 ``derivation``。

    Args:
        store: 提供落盘根目录的 Memory Store。
        rel_dir: 相对于 Store 根目录的产物目录。
        meta_type: 写入 frontmatter 的产物类型。
        context: 提供会话、片段、日期和来源信息的持久化上下文。
        body: 要写入的 Markdown 正文。

    Returns:
        文件写入成功时为 ``True``；正文为空而跳过写入时为 ``False``。

    Raises:
        OSError: 无法创建产物目录或写入文件。
    """
    if not body.strip():
        return False
    path = store.root / rel_dir / f"{context.cc_session_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    frontmatter: dict[str, Any] = {
        "type": meta_type,
        "cc_session_id": context.cc_session_id,
        "segment_id": context.segment_id,
        "review_date": context.review_date,
    }
    if context.completed_segment is not None:
        frontmatter["source"] = completed_segment_to_dict(context.completed_segment)
    if context.derivation is not None:
        frontmatter["derivation"] = derivation_to_dict(context.derivation)
    path.write_text(
        _dump_frontmatter(frontmatter, body.strip() + "\n"),
        encoding="utf-8",
    )
    return True
