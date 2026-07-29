"""对四类 Memory frontmatter 执行写入前的局部校验。

校验按调用方传入的条目类型分派，不要求 frontmatter 自带或匹配 ``type``；
未知字段和未列出的已知字段有意忽略，以兼容旧数据与扩展字段。
"""

from __future__ import annotations

from typing import Any

from trowel_py.memory.prompt import NOTE_KINDS
from trowel_py.memory.types import ValidationResult

_ENTRY_TYPES = ("core", "note", "diary", "dictionary")

_VERIFICATION = {"verified", "inferred-untested", "event-data-supported"}
# Note kind 直接复用提炼 prompt 的集合，使生成约束和写入校验保持一致。
_NOTE_KIND = set(NOTE_KINDS)
_DIARY_LAYER = {"day", "week", "month"}
_DICT_LAYER = {"L0", "L1"}
_SCOPE = {"high-risk", "low-risk"}
_CORE_STATUS = {"seed", "trial", "active", "retired"}
_NOTE_STATUS = {"active", "contradicted", "superseded", "retired"}


def validate_entry(entry_type: str, fm: dict[str, Any]) -> ValidationResult:
    """按显式条目类型校验 frontmatter。

    先检查条目类型，未知类型直接返回单个错误。已知类型仅接受 dict，其他
    Mapping 也会返回单个错误；字典输入按固定字段规则累积错误。枚举值求值
    为真但不可哈希时，底层集合成员检查的 ``TypeError`` 会直接传播。

    Args:
        entry_type: ``core``、``note``、``diary`` 或 ``dictionary``。
        fm: 待检查的 frontmatter；运行时仍会拒绝非字典值。

    Returns:
        是否通过及按检查顺序排列的错误元组。
    """
    if entry_type not in _ENTRY_TYPES:
        return ValidationResult(False, (f"unknown entry type: {entry_type!r}",))
    if not isinstance(fm, dict):
        return ValidationResult(False, ("frontmatter must be a mapping",))

    errors: list[str] = []
    if entry_type == "note":
        _validate_note(fm, errors)
    elif entry_type == "diary":
        _validate_diary(fm, errors)
    elif entry_type == "core":
        _validate_core(fm, errors)
    elif entry_type == "dictionary":
        _validate_dictionary(fm, errors)
    return ValidationResult(ok=not errors, errors=tuple(errors))


def _validate_note(fm: dict[str, Any], errors: list[str]) -> None:
    """累积 Note 标题、证据、枚举、计数和集合字段错误。

    只验证集合字段本身是 list，不检查元素类型；其他未列出的 Note 字段忽略。
    """
    title = fm.get("title")
    if not isinstance(title, str) or not title.strip():
        errors.append("note: 'title' is required and must be a non-empty string")
    verification = fm.get("verification")
    if not verification:
        errors.append("note: 'verification' is required (C-3)")
    elif verification not in _VERIFICATION:
        errors.append(
            f"note: 'verification' must be one of {sorted(_VERIFICATION)}, "
            f"got {verification!r}"
        )
    # 缺失 kind 由读取层按 fact 处理；本层只校验求值为真的显式值。
    _enum(fm, "kind", _NOTE_KIND, errors, prefix="note")
    # 旧 note 的 status 缺失或为假值时，读取层先看 retired，否则按 active 处理。
    _enum(fm, "status", _NOTE_STATUS, errors, prefix="note")
    _int_field(fm, "refs", errors, prefix="note")
    _int_field(fm, "read_sessions", errors, prefix="note")
    _int_field(fm, "pain", errors, prefix="note")
    _int_field(fm, "helpful_refs", errors, prefix="note")
    _int_field(fm, "harmful_refs", errors, prefix="note")
    tags = fm.get("tags")
    if tags is not None and not isinstance(tags, list):
        errors.append("note: 'tags' must be a list when present")
    conflicts = fm.get("conflicts_with")
    if conflicts is not None and not isinstance(conflicts, list):
        errors.append("note: 'conflicts_with' must be a list when present")
    for key in (
        "supersedes",
        "sources",
        "source_sessions",
        "source_segments",
        "derivations",
    ):
        val = fm.get(key)
        if val is not None and not isinstance(val, list):
            errors.append(f"note: '{key}' must be a list when present")
    superseded_by = fm.get("superseded_by")
    if superseded_by is not None and not isinstance(superseded_by, str):
        errors.append("note: 'superseded_by' must be a string when present")


def _validate_diary(fm: dict[str, Any], errors: list[str]) -> None:
    """累积 Diary 日期、必填层级和可选晋升列表的错误。"""
    date = fm.get("date")
    if not isinstance(date, str) or not date.strip():
        errors.append("diary: 'date' is required")
    _require_enum(fm, "layer", _DIARY_LAYER, errors, prefix="diary")
    promoted = fm.get("promoted_knowledge")
    if promoted is not None and not isinstance(promoted, list):
        errors.append("diary: 'promoted_knowledge' must be a list when present")


def _validate_core(fm: dict[str, Any], errors: list[str]) -> None:
    """校验非空 Core 条目列表，并累积每项的标识、命令和枚举错误。

    ``items`` 不是非空列表时只追加一条错误并停止；非映射项逐条报错。每项的
    其他字段，包括 ``source``，不在此校验。
    """
    items = fm.get("items")
    if not isinstance(items, list) or not items:
        errors.append("core: 'items' must be a non-empty list")
        return
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            errors.append(f"core: item[{i}] must be a mapping")
            continue
        if not isinstance(item.get("id"), str) or not item["id"].strip():
            errors.append(f"core: item[{i}] missing 'id'")
        if (
            not isinstance(item.get("imperative"), str)
            or not item["imperative"].strip()
        ):
            errors.append(f"core: item[{i}] missing 'imperative'")
        _enum(item, "scope", _SCOPE, errors, prefix=f"core item[{i}]")
        _enum(item, "status", _CORE_STATUS, errors, prefix=f"core item[{i}]")


def _validate_dictionary(fm: dict[str, Any], errors: list[str]) -> None:
    """要求 Dictionary 的 layer 是 ``L0`` 或 ``L1``。"""
    _require_enum(fm, "layer", _DICT_LAYER, errors, prefix="dictionary")


def _enum(
    fm: dict[str, Any], key: str, allowed: set[str], errors: list[str], *, prefix: str
) -> None:
    """仅在枚举值求值为真时检查成员关系。

    因此缺失值和任何假值都会被接受；真值不可哈希时 ``TypeError`` 向上传播。
    """
    val = fm.get(key)
    if val and val not in allowed:
        errors.append(
            f"{prefix}: '{key}' must be one of {sorted(allowed)}, got {val!r}"
        )


def _require_enum(
    fm: dict[str, Any], key: str, allowed: set[str], errors: list[str], *, prefix: str
) -> None:
    """把假值视为缺失，否则检查枚举成员关系。

    真值不可哈希时 ``TypeError`` 向上传播。
    """
    val = fm.get(key)
    if not val:
        errors.append(f"{prefix}: '{key}' is required")
    elif val not in allowed:
        errors.append(
            f"{prefix}: '{key}' must be one of {sorted(allowed)}, got {val!r}"
        )


def _int_field(fm: dict[str, Any], key: str, errors: list[str], *, prefix: str) -> None:
    """允许缺失或 ``None``，否则要求真正的 int 而非 bool。"""
    val = fm.get(key)
    if val is None:
        return
    if isinstance(val, bool) or not isinstance(val, int):
        # isinstance(True, int) 为真，计数契约仍必须显式拒绝 bool。
        errors.append(f"{prefix}: '{key}' must be an integer, got {val!r}")


def _bool_field(
    fm: dict[str, Any], key: str, errors: list[str], *, prefix: str
) -> None:
    """允许缺失或 ``None``，否则要求 bool。"""
    val = fm.get(key)
    if val is None:
        return
    if not isinstance(val, bool):
        errors.append(f"{prefix}: '{key}' must be a boolean, got {val!r}")
