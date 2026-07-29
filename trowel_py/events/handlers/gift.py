"""生成宠物赠送随机物品的事件。"""

from trowel_py.events.types import GameState
from trowel_py.events.handlers.types import (
    EventDependencies,
    EventHandler as EventHandler,
    EventResult,
)

_GIFT_ITEMS = ("food_basic", "food_premium", "hat_straw")


class GiftHandler:
    """从礼物池中随机生成一份宠物赠礼。"""

    def can_trigger(self, state: GameState) -> bool:
        """允许事件继续执行；卡片数量和冷却已由事件引擎过滤。"""

        return True

    def execute(self, state: GameState, deps: EventDependencies) -> EventResult:
        """随机选择一种物品并生成赠礼事件结果。"""

        item_id = deps.rng.choice(_GIFT_ITEMS)
        return EventResult(
            event_type="gift",
            description=f"宠物送了你一个 {item_id}",
            xp=10,
            item_id=item_id,
        )
