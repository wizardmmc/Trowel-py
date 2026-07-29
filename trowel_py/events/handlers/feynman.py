"""提供尚未开放的费曼事件占位实现。"""

from trowel_py.events.types import GameState
from trowel_py.events.handlers.types import (
    EventDependencies,
    EventHandler as EventHandler,
    EventResult,
)


class FeynmanHandler:
    """拒绝尚未开放的费曼事件，并保留统一处理器接口。"""

    def can_trigger(self, state: GameState) -> bool:
        """固定拒绝尚未开放的费曼事件。"""

        return False

    def execute(self, state: GameState, deps: EventDependencies) -> EventResult:
        """返回不发放奖励的未开放提示。"""

        return EventResult(
            event_type="feynman",
            description="该模式尚未开放",
            xp=0,
        )
