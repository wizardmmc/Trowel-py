"""事件 handler 的输入依赖、纯结果与行为协议。"""

from __future__ import annotations
import random
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from trowel_py.events.types import EventType, GameState
from trowel_py.player.repository import PlayerRepository
from trowel_py.events.repository import EventRepository
from trowel_py.review.repository import ReviewRepository
from trowel_py.cards.repository import CardRepository
from trowel_py.garden.repository import GardenRepository


@dataclass(frozen=True)
class EventResult:
    """只描述事件与奖励意图；handler 不在此阶段写数据库。"""

    event_type: EventType
    description: str
    xp: int = 0
    coins: int = 0
    item_id: str | None = None
    card_id: str | None = None


@dataclass(frozen=True)
class EventDependencies:
    player_repo: PlayerRepository
    review_repo: ReviewRepository
    card_repo: CardRepository
    garden_repo: GardenRepository | None
    event_repo: EventRepository
    now: datetime
    rng: random.Random


class EventHandler(Protocol):
    def can_trigger(self, state: GameState) -> bool: ...
    def execute(self, state: GameState, deps: EventDependencies) -> EventResult: ...
