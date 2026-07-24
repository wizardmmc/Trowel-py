from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from trowel_py.cards.models import Card


class ExtractRequest(BaseModel):
    """提交给卡片提取流程的原始内容。"""

    content: str = Field(min_length=1)


class ExtractedCard(BaseModel):
    title: str = Field(min_length=1)
    category: str = Field(min_length=1)
    explanation: str = Field(min_length=10)
    example: str | None = None
    difficulty: int = Field(default=3, ge=1, le=5)
    tags: list[str]
    confidence: int = Field(default=3, ge=1, le=5)
    source_type: Literal["chat", "git_diff", "cli", "general"]


class ExtractOutput(BaseModel):
    cards: list[ExtractedCard]


class CardDraft(ExtractedCard):
    """尚未持久化的卡片草稿。"""

    id: str = Field(min_length=1, max_length=64)
    source: str | None = None


class ReviewRequest(BaseModel):
    """审核卡片草稿并携带可选字段修改。"""

    action: Literal["accept", "edit", "reject"]
    edits: dict | None = None  # 例如 {"title": "新标题", "difficulty": 4}


class CardListResponse(BaseModel):
    """卡片分页列表。"""

    data: list[Card]
    total: int
    page: int
    limit: int


class ReExplainRequest(BaseModel):
    """草稿尚未持久化，因此直接携带内容；``user_hint`` 为空时自由生成。"""

    explanation: str = Field(min_length=10)
    title: str = Field(min_length=1)
    category: str = Field(min_length=1)
    user_hint: str | None = None


class ReExplainResultSchema(BaseModel):
    """重新生成解释的结构化输出，长度约束与提取结果保持一致。"""

    explanation: str = Field(min_length=10)


class FollowUpMessageSchema(BaseModel):
    """单条追问消息的结构化输出；``role`` 值域必须与数据库约束一致。"""

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)
