"""处理 ``profile.md`` 五段正文的序列化、宽松解析与写前校验。

frontmatter 和文件 IO 由 Store 负责，本模块只处理正文与 ``Profile`` 值对象。
"""

from __future__ import annotations

import re
from typing import get_args

from trowel_py.profile.models import Profile, ProfileSource

# 字典插入顺序决定 profile.md 五段正文的固定写出顺序。
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

# 校验允许的来源直接从 ProfileSource 派生，避免两处取值范围不一致。
_VALID_SOURCES: frozenset[str] = frozenset(get_args(ProfileSource))


def empty_profile() -> Profile:
    """构造读取缺失或无效 ``profile.md`` 时使用的空 Profile。

    Returns:
        五个维度和 ``updated`` 均为空、``source`` 使用默认
        ``user-edit`` 的 Profile。
    """
    return Profile()


def profile_to_body(p: Profile) -> str:
    """把 Profile 的五个维度序列化为 Markdown 正文。

    五个二级标题始终按固定顺序写出，空维度也保留标题，全文以换行结束。
    ``updated`` 和 ``source`` 属于 frontmatter，不进入正文。本函数不执行
    ``validate_profile``。

    Args:
        p: 提供五个正文维度的 Profile。

    Returns:
        包含全部五段的 Markdown 正文。
    """
    parts: list[str] = []
    for field, title in _FIELD_TO_TITLE.items():
        parts.append(f"## {title}\n{getattr(p, field)}")
    return "\n\n".join(parts) + "\n"


def body_to_profile(body: str, *, updated: str, source: str) -> Profile:
    """按五个已知二级标题宽松解析正文。

    仅识别顶格书写、``##`` 后带空白且标题文本恰好属于五个已知标题的整行。
    已知标题切换维度且不进入内容；重复标题的内容按出现顺序并入同一维度，
    缺失维度为空。首个已知标题前的文本被忽略；此后的未知标题和普通文本保留
    在当前维度，直到遇到下一个已知标题。

    解析使用 ``splitlines()`` 并以 ``\n`` 重组，因此不保留原始换行符；
    维度内部的行和空行保留，但整体首尾空白会被删除。``updated`` 与
    ``source`` 原样来自调用方，本函数不执行校验。

    Args:
        body: 不含 frontmatter 的 Profile Markdown 正文。
        updated: 写入返回对象的更新时间文本。
        source: 写入返回对象的来源文本。

    Returns:
        由已知段落及调用方元数据构造的 Profile。
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
    """校验 Profile 正文维度、更新时间和本次写入来源。

    五个维度必须是字符串，``updated`` 必须是非空白字符串，但不会解析日期
    格式。``source`` 必须属于 ``ProfileSource``；它是本次写入的权威来源，
    ``p.source`` 不参与校验。冻结数据类本身不执行这些运行时类型检查。所有
    失败项会在一次检查中汇总，并通过同一个 ``ValueError`` 报告。

    Args:
        p: 要在写入前校验的 Profile。
        source: 本次写入将记录的来源。

    Raises:
        ValueError: 汇总任一维度不是字符串、``updated`` 不是字符串或为空白、
            来源不受支持等错误。
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
