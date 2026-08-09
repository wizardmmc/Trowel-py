"""定义卡片提取、审核、列表、重新解释和追问消息的数据模型。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from trowel_py.cards.models import Card


class ExtractRequest(BaseModel):
    """携带要交给卡片提取流程的原始内容。

    Attributes:
        content: 原始文本或 Claude Code JSONL 对话记录，至少包含一个字符。
    """

    content: str = Field(min_length=1)


class ExtractedCard(BaseModel):
    """记录模型从输入内容中提取的一张卡片候选。

    Attributes:
        title: 候选卡片标题，不能为空。
        category: 候选卡片所属分类，不能为空。
        explanation: 候选卡片的主要解释，至少包含 10 个字符。
        example: 帮助理解解释的示例；模型未提供时为 ``None``。
        difficulty: 难度值，范围为 1 到 5，默认为 3。
        tags: 模型为候选卡片提取的标签，可以为空列表。
        confidence: 模型给出的置信度分值，范围为 1 到 5，默认为 3。
        source_type: 输入内容的来源，只能是 ``"chat"``、``"git_diff"``、
            ``"cli"`` 或 ``"general"``。
    """

    title: str = Field(min_length=1)
    category: str = Field(min_length=1)
    explanation: str = Field(min_length=10)
    example: str | None = None
    difficulty: int = Field(default=3, ge=1, le=5)
    tags: list[str]
    confidence: int = Field(default=3, ge=1, le=5)
    source_type: Literal["chat", "git_diff", "cli", "general"]


class ExtractOutput(BaseModel):
    """记录一次模型提取返回的全部卡片候选。

    Attributes:
        cards: 本次提取的候选卡片；没有提取结果时为空列表。
    """

    cards: list[ExtractedCard]


class CardDraft(ExtractedCard):
    """表示保存在进程内、等待审核的卡片草稿。

    草稿继承候选卡片的内容、评分和来源字段，并增加以下字段。

    Attributes:
        id: 草稿的唯一 ID，由卡片提取流程生成。
        source: 审核通过后写入 ``Card.source`` 的来源标识；提取流程会复制
            ``source_type``，未单独设置时为 ``None``。
    """

    id: str = Field(min_length=1, max_length=64)
    source: str | None = None


class ReviewRequest(BaseModel):
    """描述如何处理一张卡片草稿。

    Attributes:
        action: 审核动作；``"accept"`` 原样保存，``"edit"`` 应用 ``edits`` 后
            保存，``"reject"`` 不保存卡片。
        edits: ``action`` 为 ``"edit"`` 时覆盖草稿字段的键值；没有修改时为
            ``None``。模型不限制 ``action`` 与 ``edits`` 的组合：``"accept"``
            或 ``"reject"`` 携带的修改会被忽略，``"edit"`` 没有修改时会原样保存。
    """

    action: Literal["accept", "edit", "reject"]
    edits: dict[str, Any] | None = None


class CardListResponse(BaseModel):
    """定义一组卡片及其分页值。

    Attributes:
        data: 当前页中的卡片。
        total: 分页前的卡片总数。
        page: 当前请求的页码。
        limit: 当前请求的每页数量。
    """

    data: list[Card]
    total: int
    page: int
    limit: int


class ReExplainRequest(BaseModel):
    """携带重新生成草稿解释所需的当前内容和用户偏好。

    草稿尚未保存，因此请求直接携带内容而不是卡片 ID。

    Attributes:
        explanation: 当前解释，模型会根据它生成新的候选版本；至少包含 10 个字符。
        title: 草稿标题，不能为空。
        category: 草稿分类，不能为空。
        user_hint: 用户希望采用的解释方向；为 ``None`` 或空字符串时不限定方向。
    """

    explanation: str = Field(min_length=10)
    title: str = Field(min_length=1)
    category: str = Field(min_length=1)
    user_hint: str | None = None


class ReExplainResultSchema(BaseModel):
    """记录模型重新生成的一版候选解释。

    Attributes:
        explanation: 候选解释，至少包含 10 个字符。
    """

    explanation: str = Field(min_length=10)


class FollowUpMessageSchema(BaseModel):
    """记录卡片追问中的一条用户或助手消息。

    Attributes:
        role: 消息发送方，只能是 ``"user"`` 或 ``"assistant"``。
        content: 消息正文，至少包含一个字符。
    """

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)
