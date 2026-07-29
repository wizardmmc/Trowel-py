"""事件引擎的不可变值对象。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

EventType = Literal[
    "sign_in",
    "challenge",
    "discovery",
    "story",
    "growth",
    "gift",
    "feynman",
]


@dataclass(frozen=True)
class EventConfig:
    """保存一种事件的随机权重、冷却时间和卡片数量门槛。"""

    type: EventType
    weight: int
    cooldown_minutes: int
    min_cards: int


@dataclass(frozen=True)
class GameState:
    """只保存事件判断需要的状态，避免选择逻辑依赖卡片数据库结构。"""

    total_cards: int
    due_cards: int
    player_level: int
    streak_days: int
    learned_card_ids: tuple[str, ...] = ()
