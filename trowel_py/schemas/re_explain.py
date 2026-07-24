from __future__ import annotations

from pydantic import BaseModel, Field


class ReExplainRequest(BaseModel):
    """草稿尚未持久化，因此直接携带内容；``user_hint`` 为空时自由生成。"""

    explanation: str = Field(min_length=10)
    title: str = Field(min_length=1)
    category: str = Field(min_length=1)
    user_hint: str | None = None


class ReExplainResultSchema(BaseModel):
    """重新生成解释的结构化输出，长度约束与提取结果保持一致。"""

    explanation: str = Field(min_length=10)
