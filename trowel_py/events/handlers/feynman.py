from trowel_py.events.types import GameState
from trowel_py.events.handlers.types import (
    EventDependencies,
    EventHandler as EventHandler,
    EventResult,
)


class FeynmanHandler:
    def can_trigger(self, state: GameState) -> bool:
        return False

    def execute(self, state: GameState, deps: EventDependencies) -> EventResult:
        return EventResult(
            event_type="feynman",
            description="该模式尚未开放",
            xp=0,
        )
