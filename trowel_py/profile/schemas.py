"""profile HTTP 接口的 Pydantic DTO。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from trowel_py.memory.types import ProfileDimension, SuggestionStatus


class ProfileUpdate(BaseModel):
    """五个可编辑画像维度及文件级来源；更新时间由服务端写入。"""

    ability: str = ""
    methodology: str = ""
    expression: str = ""
    goal: str = ""
    other: str = ""
    source: Literal["user-edit", "ai-calibration"] = "user-edit"


class ProfileDTO(BaseModel):
    """画像维度及来源信息。"""

    ability: str
    methodology: str
    expression: str
    goal: str
    other: str
    updated: str
    source: str


class SuggestionDTO(BaseModel):
    """等待用户决策的画像建议。"""

    id: str
    dimension: ProfileDimension
    body: str
    sources: list[str]
    date: str
    status: SuggestionStatus


class SuggestionStatusUpdate(BaseModel):
    """用户对画像建议的接受或丢弃决定。"""

    status: Literal["accepted", "discarded"]
