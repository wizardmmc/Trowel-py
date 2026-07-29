"""生成随机物品发现事件。"""

from trowel_py.events.types import GameState
from trowel_py.events.handlers.types import (
    EventDependencies,
    EventHandler as EventHandler,
    EventResult,
)

# 商品 ID 必须与 player.service.ITEM_PRICES 同步。
_DISCOVERY_ITEMS = (
    "food_basic",
    "food_premium",
    "hat_straw",
    "hat_scholar",
    "hat_wreath",
)


class DiscoveryHandler:
    """从发现物品池中随机生成一份物品奖励。"""

    def can_trigger(self, state: GameState) -> bool:
        """允许事件继续执行；卡片数量和冷却已由事件引擎过滤。"""

        # 冷却与卡片数量门槛统一由事件引擎判断。
        return True

    def execute(self, state: GameState, deps: EventDependencies) -> EventResult:
        """随机选择一种物品并生成发现事件结果。"""

        item_id = deps.rng.choice(_DISCOVERY_ITEMS)
        return EventResult(
            event_type="discovery",
            description=f"你在角落里发现了一个{item_id}",
            xp=10,
            item_id=item_id,
        )
