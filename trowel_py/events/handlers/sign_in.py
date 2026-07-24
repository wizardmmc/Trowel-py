from trowel_py.events.types import GameState
from trowel_py.events.handlers.types import (
    EventDependencies,
    EventHandler as EventHandler,
    EventResult,
)
from trowel_py.player.service import update_streak

_BASE_SIGN_IN_XP = 20
_STREAK_BONUS_PER_DAY = 5
_STREAK_BONUS_CAP = 50


class SignInHandler:
    def can_trigger(self, state: GameState) -> bool:
        # 同日重复触发由配置中的 1440 分钟冷却阻止。
        return True

    def execute(self, state: GameState, deps: EventDependencies) -> EventResult:
        new_streak = update_streak(deps.player_repo, deps.now)

        bonus = min((new_streak - 1) * _STREAK_BONUS_PER_DAY, _STREAK_BONUS_CAP)
        xp = _BASE_SIGN_IN_XP + bonus
        return EventResult(
            event_type="sign_in", description=f"连续签到第 {new_streak} 天", xp=xp
        )
