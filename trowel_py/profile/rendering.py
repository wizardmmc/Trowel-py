"""把非空用户画像维度渲染为会话系统提示词。"""

from __future__ import annotations

from typing import Protocol

from trowel_py.profile.document import _FIELD_TO_TITLE
from trowel_py.profile.models import Profile


class ProfileReader(Protocol):
    """约束画像渲染所需的只读仓储接口。"""

    def load_profile(self) -> Profile:
        """返回当前用户画像。"""
        ...


def render_profile(reader: ProfileReader) -> str:
    """按标准字段顺序渲染正文非空的画像维度。

    Args:
        reader: 提供当前画像的只读仓储。

    Returns:
        带 ``# 用户画像`` 标题的提示词文本；所有维度为空时返回空字符串。
    """
    profile = reader.load_profile()
    blocks = [
        f"## {_FIELD_TO_TITLE[field]}\n{getattr(profile, field)}"
        for field in _FIELD_TO_TITLE
        if getattr(profile, field).strip()
    ]
    if not blocks:
        return ""
    return "# 用户画像\n\n" + "\n\n".join(blocks)
