"""定义事件处理器需要的外部对象、执行结果和统一接口。"""

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
    """保存事件内容和待发放奖励；事件处理器在此阶段不写数据库。"""

    event_type: EventType
    description: str
    xp: int = 0
    coins: int = 0
    item_id: str | None = None
    card_id: str | None = None


@dataclass(frozen=True)
class EventDependencies:
    """集中保存事件处理器需要的数据读写对象、时间和随机数。"""

    player_repo: PlayerRepository
    review_repo: ReviewRepository
    card_repo: CardRepository
    garden_repo: GardenRepository | None
    event_repo: EventRepository
    now: datetime
    rng: random.Random


class EventHandler(Protocol):
    """规定所有事件处理器都要提供的判断和执行操作。"""

    def can_trigger(self, state: GameState) -> bool:
        """判断当前游戏状态是否允许处理器执行。"""

        ...

    def execute(self, state: GameState, deps: EventDependencies) -> EventResult:
        """执行事件并返回待发放的奖励和展示内容。"""

        ...
