"""定义数据库和接口共用的卡片模型。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class Card(BaseModel):
    """表示一张已经保存或准备保存的卡片。

    Attributes:
        id: 卡片的唯一 ID，由调用方提供。
        title: 卡片标题，不能为空。
        category: 用于归类卡片的名称，不能为空。
        explanation: 卡片的主要解释内容，至少包含 10 个字符。
        example: 帮助理解解释的示例；没有示例时为 ``None``。
        difficulty: 难度值，范围为 1 到 5，默认为 3。
        source: 卡片内容的来源标识；没有记录来源时为 ``None``。
        tags: 用于检索或分组的标签；没有标签时为空列表。
        status: 卡片状态，只能是 ``"active"``、``"archived"`` 或 ``"draft"``，
            默认为 ``"active"``。
        created_at: 卡片创建时间，未传入时取模型创建时的本地时间。
        updated_at: 卡片最后更新时间，未传入时取模型创建时的本地时间。
    """

    id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1)
    category: str = Field(min_length=1)
    explanation: str = Field(min_length=10)
    example: str | None = None
    difficulty: int = Field(default=3, ge=1, le=5)
    source: str | None = None
    tags: list[str] = Field(default_factory=list)
    status: Literal["active", "archived", "draft"] = "active"
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
