"""Memory frontmatter 写入校验；未知字段有意忽略以兼容扩展字段。"""

from __future__ import annotations

from typing import Any

from trowel_py.memory.prompt import NOTE_KINDS
from trowel_py.memory.types import ValidationResult

_ENTRY_TYPES = ("core", "note", "diary", "dictionary")

_VERIFICATION = {"verified", "inferred-untested", "event-data-supported"}
# 复用 agent prompt 的种类集合，避免生成约束与写入校验漂移。
_NOTE_KIND = set(NOTE_KINDS)
_DIARY_LAYER = {"day", "week", "month"}
_DICT_LAYER = {"L0", "L1"}
_SCOPE = {"high-risk", "low-risk"}
_CORE_STATUS = {"seed", "trial", "active", "retired"}
_NOTE_STATUS = {"active", "contradicted", "superseded", "retired"}


def validate_entry(entry_type: str, fm: dict[str, Any]) -> ValidationResult:
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
    # 缺失 kind 由读取或持久化层按 fact 处理。
    _enum(fm, "kind", _NOTE_KIND, errors, prefix="note")
    # 旧 note 缺失 status 时，读取层先解释 retired，否则按 active 处理。
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
    for key in ("supersedes", "sources", "source_sessions"):
        val = fm.get(key)
        if val is not None and not isinstance(val, list):
            errors.append(f"note: '{key}' must be a list when present")
    superseded_by = fm.get("superseded_by")
    if superseded_by is not None and not isinstance(superseded_by, str):
        errors.append("note: 'superseded_by' must be a string when present")


def _validate_diary(fm: dict[str, Any], errors: list[str]) -> None:
    date = fm.get("date")
    if not isinstance(date, str) or not date.strip():
        errors.append("diary: 'date' is required")
    _require_enum(fm, "layer", _DIARY_LAYER, errors, prefix="diary")
    promoted = fm.get("promoted_knowledge")
    if promoted is not None and not isinstance(promoted, list):
        errors.append("diary: 'promoted_knowledge' must be a list when present")


def _validate_core(fm: dict[str, Any], errors: list[str]) -> None:
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
    _require_enum(fm, "layer", _DICT_LAYER, errors, prefix="dictionary")


def _enum(
    fm: dict[str, Any], key: str, allowed: set[str], errors: list[str], *, prefix: str
) -> None:
    val = fm.get(key)
    if val and val not in allowed:
        errors.append(
            f"{prefix}: '{key}' must be one of {sorted(allowed)}, got {val!r}"
        )


def _require_enum(
    fm: dict[str, Any], key: str, allowed: set[str], errors: list[str], *, prefix: str
) -> None:
    val = fm.get(key)
    if not val:
        errors.append(f"{prefix}: '{key}' is required")
    elif val not in allowed:
        errors.append(
            f"{prefix}: '{key}' must be one of {sorted(allowed)}, got {val!r}"
        )


def _int_field(fm: dict[str, Any], key: str, errors: list[str], *, prefix: str) -> None:
    val = fm.get(key)
    if val is None:
        return
    if isinstance(val, bool) or not isinstance(val, int):
        # bool 是 int 的子类，必须显式排除。
        errors.append(f"{prefix}: '{key}' must be an integer, got {val!r}")


def _bool_field(
    fm: dict[str, Any], key: str, errors: list[str], *, prefix: str
) -> None:
    val = fm.get(key)
    if val is None:
        return
    if not isinstance(val, bool):
        errors.append(f"{prefix}: '{key}' must be a boolean, got {val!r}")
