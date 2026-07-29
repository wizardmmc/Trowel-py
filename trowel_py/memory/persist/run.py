"""编排 Draft 到 Note、Episode、附属产物和 completion manifest 的落盘。"""

from __future__ import annotations

import json
from datetime import datetime

from trowel_py.memory.draft import Draft
from trowel_py.memory.store import MemoryStore
from trowel_py.memory.provenance import completed_segment_to_dict, derivation_to_dict
from trowel_py.memory.types import PersistContext

from .artifacts import _write_meta
from .manifest import (
    _SEGMENTS_META_DIR,
    _manifest_intact,
    _report_from_manifest,
)
from .models import PersistReport
from .notes import _content_hash, _update_note, _write_new_note

_REFLECTIONS_DIR = "meta/reflections"
_ESCALATIONS_DIR = "meta/escalations"


def persist_draft(
    store: MemoryStore,
    draft: Draft,
    context: PersistContext,
) -> PersistReport:
    """持久化一份已校验 Draft，并在最后写入 completion manifest。

    始终先用 ``context.segment_id`` 定位 manifest。命中后，若 manifest 已
    声明的产物路径都存在，则忽略 Draft 和 Context 的其余内容，不执行写入，
    直接恢复幂等跳过报告。这里的完整性只检查已声明路径是否存在，不读取产物
    内容，也不要求 manifest 声明所有产物字段。

    正常落盘按 Note、Episode、reflection、escalation、manifest 的顺序执行。
    Note 身份由 ``context.cc_session_id`` 和标题、摘要、正文、类别的内容哈希
    共同确定；命中后只重判并合并来源，不覆盖这些身份内容。空 reflection 会
    跳过；escalation 丢弃纯空白项后渲染为项目符号。两者为空时都不会创建、
    覆盖或删除已有同会话文件。

    manifest 记录片段、会话、复核日期、新建或更新的 Note ID、各产物路径，
    以及无时区且精确到秒的本机 ``completed_at``；已封口片段和派生信息存在
    时，才分别写入 ``source`` 和 ``derivation``。

    本函数不会调用 ``validate_draft``，调用方须在进入此处前完成校验。落盘
    不是事务：任一步失败都会原样抛出异常，之前已写的产物不会回滚。可解析但
    产物缺失的旧 manifest 会保留到最后，并在重跑成功时覆盖；无效 JSON 或
    非对象 manifest 会在任何重跑写入前直接报错。畸形字段可能报错，也可能
    因未做 schema 校验而被当作未声明或通过检查，均不会自动修复。completion
    manifest 虽在其余产物之后写入，但使用普通 ``write_text`` 而非原子替换，
    写入失败可能留下截断或无效文件。

    Args:
        store: 接收全部持久化产物的 Memory Store。
        draft: 已通过落盘前校验的提炼草稿。
        context: 提供片段身份、会话、日期和来源的持久化上下文。

    Returns:
        本次新建、更新和产物状态，或从完整 manifest 恢复的幂等跳过报告。

    Raises:
        json.JSONDecodeError: 已有 manifest 不是有效的 JSON。
        UnicodeDecodeError: 已有 manifest、Note 或 Episode 不是有效的
            UTF-8 文本。
        OSError: 无法读取、创建或写入任一产物。
        ValueError: Note 未通过 Store 校验，或既有 Note frontmatter 无效。
        AttributeError: 已有 manifest 的顶层结构不是对象。
        KeyError: 已有完整 manifest 缺少恢复报告所需的字段。
        TypeError: 已有 manifest 的字段结构无法用于完整性检查或报告恢复。
    """
    root = store.root
    segment_meta = root / _SEGMENTS_META_DIR
    manifest_path = segment_meta / f"{context.segment_id}.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if _manifest_intact(root, manifest):
            return _report_from_manifest(manifest)

    today = context.review_date
    created: list[str] = []
    updated: list[str] = []
    verification_counts: dict[str, int] = {}
    for note in draft.notes:
        content_hash = _content_hash(note)
        verification_counts[note.verification] = (
            verification_counts.get(note.verification, 0) + 1
        )
        existing = store.find_note_by_source(
            context.cc_session_id,
            content_hash,
        )
        if existing is not None:
            _update_note(
                store,
                existing,
                note,
                content_hash,
                context,
                today,
            )
            updated.append(existing)
        else:
            created.append(
                _write_new_note(
                    store,
                    note,
                    content_hash,
                    context,
                    today,
                )
            )

    store.write_episode(context, draft.diary)
    reflection_written = _write_meta(
        store,
        _REFLECTIONS_DIR,
        "reflection",
        context,
        draft.reflection,
    )
    escalation_items = [item for item in draft.escalate_to_human if item.strip()]
    escalation_written = _write_meta(
        store,
        _ESCALATIONS_DIR,
        "escalation",
        context,
        "\n".join(f"- {item}" for item in escalation_items),
    )

    manifest = {
        "segment_id": context.segment_id,
        "cc_session_id": context.cc_session_id,
        "review_date": context.review_date,
        "completed_at": datetime.now().isoformat(timespec="seconds"),
        "notes_created": tuple(created),
        "notes_updated": tuple(updated),
        "episode_file": f"episodes/{context.cc_session_id}.md",
        "reflection_file": (
            f"{_REFLECTIONS_DIR}/{context.cc_session_id}.md"
            if reflection_written
            else None
        ),
        "escalation_file": (
            f"{_ESCALATIONS_DIR}/{context.cc_session_id}.md"
            if escalation_written
            else None
        ),
    }
    if context.completed_segment is not None:
        manifest["source"] = completed_segment_to_dict(context.completed_segment)
    if context.derivation is not None:
        manifest["derivation"] = derivation_to_dict(context.derivation)
    segment_meta.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return PersistReport(
        notes_written=len(created) + len(updated),
        diary_written=1,
        verification_counts=verification_counts,
        notes_created=tuple(created),
        notes_updated=tuple(updated),
        notes_skipped=(),
        episode_written=True,
        reflection_written=reflection_written,
        escalation_written=escalation_written,
        manifest_path=f"{_SEGMENTS_META_DIR}/{context.segment_id}.json",
        ok=True,
    )
