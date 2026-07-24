"""profile.md 五段正文的序列化、宽松解析与写前校验。

frontmatter 和文件 IO 由 store 所有，本模块只处理正文与 ``Profile`` 值对象。
"""

from __future__ import annotations

import re
from typing import get_args

from trowel_py.memory.types import Profile, ProfileSource

# 插入顺序就是 profile.md 的固定写出顺序。
_FIELD_TO_TITLE: dict[str, str] = {
    "ability": "能力水平",
    "methodology": "方法论偏好",
    "expression": "表达风格",
    "goal": "长程目标",
    "other": "其他",
}
_TITLE_TO_FIELD: dict[str, str] = {
    title: field for field, title in _FIELD_TO_TITLE.items()
}

_HEADING_RE = re.compile(r"^##\s+(.+?)\s*$")

# 从 ProfileSource 派生，避免写入门禁与类型契约漂移。
_VALID_SOURCES: frozenset[str] = frozenset(get_args(ProfileSource))


def empty_profile() -> Profile:
    """返回缺失或空 profile.md 的非 ``None`` 哨兵。"""
    return Profile()


def profile_to_body(p: Profile) -> str:
    """按固定顺序写出全部五段；空维度也保留标题，正文以换行结束。"""
    parts: list[str] = []
    for field, title in _FIELD_TO_TITLE.items():
        parts.append(f"## {title}\n{getattr(p, field)}")
    return "\n\n".join(parts) + "\n"


def body_to_profile(body: str, *, updated: str, source: str) -> Profile:
    """按五个已知二级标题宽松解析正文。

    缺失段为空；未知二级标题保留在当前段，避免丢失手工内容。
    ``updated`` 与 ``source`` 由 frontmatter 调用方传入。
    """
    dims: dict[str, list[str]] = {field: [] for field in _FIELD_TO_TITLE}
    current: str | None = None
    for line in body.splitlines():
        m = _HEADING_RE.match(line)
        if m and (field := _TITLE_TO_FIELD.get(m.group(1).strip())) is not None:
            current = field
            continue
        if current is not None:
            dims[current].append(line)
    kwargs: dict[str, str] = {
        field: "\n".join(lines).strip() for field, lines in dims.items()
    }
    return Profile(updated=updated, source=source, **kwargs)


def validate_profile(p: Profile, source: str) -> None:
    """校验五维字符串、非空 ``updated`` 与写入来源。

    frozen dataclass 不执行运行时类型检查；此处不解析 ``updated`` 的日期格式。
    """
    errors: list[str] = []
    for field in _FIELD_TO_TITLE:
        if not isinstance(getattr(p, field), str):
            errors.append(f"profile: '{field}' must be a string")
    if not isinstance(p.updated, str) or not p.updated.strip():
        errors.append("profile: 'updated' is required (ISO date)")
    if source not in _VALID_SOURCES:
        errors.append(
            f"profile: 'source' must be one of {sorted(_VALID_SOURCES)}, got {source!r}"
        )
    if errors:
        raise ValueError(f"invalid profile: {errors}")
