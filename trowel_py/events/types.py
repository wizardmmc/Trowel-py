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
    type: EventType
    weight: int
    cooldown_minutes: int
    min_cards: int


@dataclass(frozen=True)
class GameState:
    """只携带聚合状态，避免纯引擎依赖卡片持久化结构。"""

    total_cards: int
    due_cards: int
    player_level: int
    streak_days: int
    learned_card_ids: tuple[str, ...] = ()
