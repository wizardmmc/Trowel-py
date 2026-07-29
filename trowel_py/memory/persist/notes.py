"""计算 Note 内容身份，并创建或重判提炼出的 Note。"""

from __future__ import annotations

import hashlib

from trowel_py.memory.draft import DraftNote
from trowel_py.memory.ids import uuid7
from trowel_py.memory.provenance import derivation_to_dict
from trowel_py.memory.store import MemoryStore, _split_frontmatter
from trowel_py.memory.types import PersistContext


def _content_hash(note: DraftNote) -> str:
    """计算同一来源会话内识别重复 Note 所用的内容哈希。

    输入以换行符依次连接 ``title``、``summary``、``body`` 和 ``kind``；
    verification、pain、标签、理由和冲突关系均不参与。同一来源会话中只改变
    这些字段仍会命中既有 Note，不会另建；实际可写回字段由 ``_update_note``
    限定。

    Args:
        note: 要计算内容身份的提炼候选 Note。

    Returns:
        SHA-256 摘要的前 16 个十六进制字符。
    """
    raw = "\n".join([note.title, note.summary, note.body, note.kind])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _write_new_note(
    store: MemoryStore,
    note: DraftNote,
    content_hash: str,
    context: PersistContext,
    today: str,
) -> str:
    """初始化一条 active Note 并写入 Store。

    新 Note 复制候选的标题、类别、摘要、标签、判断及理由、冲突关系和正文；
    使用 UUIDv7 生成 ``memory_id``，状态设为 active，以 ``today`` 初始化
    ``valid_from``、``created`` 和 ``updated``。``refs``、``helpful_refs``
    和 ``harmful_refs`` 置零，``last_ref`` 置空；``read_sessions`` 不写入
    frontmatter，读取时使用默认值 0。当前会话 ID 同时写入 ``sources`` 和
    ``source_sessions``；上下文带有已封口片段或派生信息时，再写入
    ``source_segments`` 或 ``derivations``。

    Args:
        store: 接收新 Note 的 Memory Store。
        note: 已通过落盘前校验的候选 Note。
        content_hash: 由 ``_content_hash`` 计算的内容身份。
        context: 提供会话、片段和派生来源的持久化上下文。
        today: 写入 ``valid_from``、``created`` 和 ``updated`` 的日期。

    Returns:
        Store 为新 Note 选择的文件 slug；该值不同于 frontmatter 中的
        ``memory_id``。

    Raises:
        OSError: 无法创建目录或写入 Note 文件。
        ValueError: 构造出的 Note 未通过 Store schema 校验。
    """
    entry = {
        "type": "note",
        "title": note.title,
        "kind": note.kind,
        "summary": note.summary,
        "tags": list(note.tags),
        "verification": note.verification,
        "verification_reason": note.verification_reason,
        "pain": note.pain,
        "pain_reason": note.pain_reason,
        "conflicts_with": list(note.conflicts_with),
        "memory_id": str(uuid7()),
        "status": "active",
        "valid_from": today,
        "created": today,
        "updated": today,
        "refs": 0,
        "helpful_refs": 0,
        "harmful_refs": 0,
        "last_ref": "",
        "sources": [context.cc_session_id],
        "source_sessions": [context.cc_session_id],
        "content_hash": content_hash,
        "__body": note.body,
    }
    if context.completed_segment is not None:
        entry["source_segments"] = [context.completed_segment.segment_id]
    if context.derivation is not None:
        entry["derivations"] = [derivation_to_dict(context.derivation)]
    return store.write_note(entry)


def _update_note(
    store: MemoryStore,
    note_id: str,
    note: DraftNote,
    content_hash: str,
    context: PersistContext,
    today: str,
) -> None:
    """更新既有 Note 的判断字段并合并来源。

    更新 verification、pain、各自理由、冲突关系、``updated`` 和内容哈希，
    并按下述规则合并来源；除此之外的 frontmatter 与正文保持不变，包括标题、
    摘要、类别、标签、``memory_id``、状态、生效/创建日期及全部引用字段。
    ``sources`` 与 ``source_sessions`` 去重排序；``source_segments`` 保留既有
    列表，仅在新 ID 尚不存在时追加；``derivations`` 也保留既有列表，仅在
    本次 ``run_id`` 尚未出现在既有字典项中时追加。

    来源合并基于调用 ``update_note_fields`` 取得文件锁之前读取的快照；调用方
    必须串行更新同一 Note，否则并发写入可能覆盖彼此刚增加的来源。

    Args:
        store: 持有既有 Note 的 Memory Store。
        note_id: 要更新的 Note 文件 slug。
        note: 提供最新判断字段的候选 Note。
        content_hash: 写回 frontmatter 的内容身份。
        context: 提供本次会话、片段和派生来源的持久化上下文。
        today: 写入 ``updated`` 的日期。

    Raises:
        FileNotFoundError: ``note_id`` 对应文件不存在。
        OSError: 无法读取或重写 Note 文件。
        ValueError: 既有 Note 没有有效的 frontmatter。
    """
    path = store.root / "notes" / f"{note_id}.md"
    frontmatter, _body = _split_frontmatter(path.read_text(encoding="utf-8"))
    sessions = set(frontmatter.get("source_sessions") or []) if frontmatter else set()
    sessions.add(context.cc_session_id)
    provenance = set(frontmatter.get("sources") or []) if frontmatter else set()
    provenance.add(context.cc_session_id)
    source_segments = (
        list(frontmatter.get("source_segments") or []) if frontmatter else []
    )
    if (
        context.completed_segment is not None
        and context.completed_segment.segment_id not in source_segments
    ):
        source_segments.append(context.completed_segment.segment_id)
    derivations = list(frontmatter.get("derivations") or []) if frontmatter else []
    if context.derivation is not None:
        encoded = derivation_to_dict(context.derivation)
        run_ids = {
            item.get("run_id")
            for item in derivations
            if isinstance(item, dict)
        }
        if context.derivation.run_id not in run_ids:
            derivations.append(encoded)
    fields = {
        "verification": note.verification,
        "verification_reason": note.verification_reason,
        "pain": note.pain,
        "pain_reason": note.pain_reason,
        "conflicts_with": list(note.conflicts_with),
        "updated": today,
        "source_sessions": sorted(sessions),
        "sources": sorted(provenance),
        "content_hash": content_hash,
    }
    if source_segments:
        fields["source_segments"] = source_segments
    if derivations:
        fields["derivations"] = derivations
    store.update_note_fields(
        note_id,
        fields,
    )
