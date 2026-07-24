from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, Field


class FollowUpMessageSchema(BaseModel):
    """单条追问消息的结构化输出；``role`` 值域必须与数据库约束一致。"""

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)
