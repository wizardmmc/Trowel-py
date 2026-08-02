"""提供 Draft 落盘硬校验和 procedure 内容软告警。"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from typing import Any

from trowel_py.memory.draft.episode import (
    DraftCorrection,
    DraftDecision,
    DraftOpenLoop,
)


def validate_draft(
    draft: Any,
    *,
    note_kinds: Collection[str],
    verification_tiers: Collection[str],
) -> list[str]:
    """按遍历顺序收集新草稿的全部落盘门禁错误。

    检查范围包括 Note 标题非空和枚举、Diary 日期非空、非空旧
    ``events``、仅在 ``items`` 为空时拒绝的旧结构化列表，以及各类结构化
    事件的必填字段和状态。事件数量和文本长度不在此处设限。

    Args:
        draft: 已解析的完整草稿。
        note_kinds: 允许写入的 Note 种类。
        verification_tiers: 允许写入的验证等级。

    Returns:
        先按顺序收集全部 Note 错误，再逐个 Diary 收集 Diary 自身及其事件
        错误；空列表表示通过门禁。
    """
    errors: list[str] = []
    for index, note in enumerate(draft.notes):
        if not note.title.strip():
            errors.append(f"notes[{index}]: missing title")
        if note.kind not in note_kinds:
            errors.append(
                f"notes[{index}] {note.title!r}: unknown kind {note.kind!r}; "
                f"expected one of {list(note_kinds)!r}"
            )
        if note.verification not in verification_tiers:
            errors.append(
                f"notes[{index}] {note.title!r}: unknown verification "
                f"{note.verification!r}"
            )

    for index, diary in enumerate(draft.diary):
        if not diary.date.strip():
            errors.append(f"diary[{index}]: missing date")
        if diary.events.strip():
            errors.append(
                f"diary[{index}]: legacy events are not allowed in a new "
                "draft; use items"
            )
        legacy_fields = tuple(
            field_name
            for field_name in (
                "outcomes",
                "decisions",
                "corrections",
                "open_loops",
            )
            if getattr(diary, field_name) and not diary.items
        )
        if legacy_fields:
            errors.append(
                f"diary[{index}]: legacy structured lists are not allowed in a new "
                "draft; use items"
            )
        for item_index, item in enumerate(diary.items):
            prefix = f"diary[{index}].items[{item_index}]"
            if isinstance(item, DraftCorrection):
                if not item.before:
                    errors.append(f"{prefix}.before must not be empty")
                if not item.after:
                    errors.append(f"{prefix}.after must not be empty")
                if not item.reason:
                    errors.append(f"{prefix}.reason must not be empty")
            else:
                if not item.summary:
                    errors.append(f"{prefix}.summary must not be empty")
            if isinstance(item, DraftDecision):
                if not item.reason:
                    errors.append(f"{prefix}.reason must not be empty")
                if item.status not in {"active", "superseded"}:
                    errors.append(
                        f"{prefix}.status must be one of ['active', 'superseded']"
                    )
            if isinstance(item, DraftOpenLoop):
                if not item.reason:
                    errors.append(f"{prefix}.reason must not be empty")
                if item.status not in {"active", "closed"}:
                    errors.append(
                        f"{prefix}.status must be one of ['active', 'closed']"
                    )
    return errors


def procedure_warnings(
    draft: Any,
    *,
    elements: Mapping[str, Sequence[str]],
) -> list[str]:
    """用关键词启发式报告 procedure Note 可能缺少的组成部分。

    仅检查 ``kind`` 恰为 ``"procedure"`` 的 Note。空正文只产生一条告警；
    其余正文通过不区分大小写的子串匹配逐项检查，告警顺序由 Note 和
    ``elements`` 的遍历顺序决定。结果不参与落盘门禁。

    Args:
        draft: 已解析的完整草稿。
        elements: 组成部分名称到可识别关键词的映射。

    Returns:
        可能缺少正文或组成部分的告警。
    """
    warnings: list[str] = []
    for index, note in enumerate(draft.notes):
        if note.kind != "procedure":
            continue
        if not note.body.strip():
            warnings.append(
                f"notes[{index}] {note.title!r}: kind=procedure but body empty"
            )
            continue
        body_lower = note.body.lower()
        for element, aliases in elements.items():
            if not any(alias.lower() in body_lower for alias in aliases):
                warnings.append(
                    f"notes[{index}] {note.title!r}: kind=procedure but body "
                    f"may miss '{element}'"
                )
    return warnings
