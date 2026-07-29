"""更新连续签到天数并计算签到经验奖励。"""

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
    """根据连续签到天数生成逐日增加的经验奖励。"""

    def can_trigger(self, state: GameState) -> bool:
        """允许事件继续执行；同日重复由事件冷却阻止。"""

        # 同日重复触发由配置中的 1440 分钟冷却阻止。
        return True

    def execute(self, state: GameState, deps: EventDependencies) -> EventResult:
        """更新连续签到记录并生成封顶的经验奖励。"""

        new_streak = update_streak(deps.player_repo, deps.now)

        bonus = min((new_streak - 1) * _STREAK_BONUS_PER_DAY, _STREAK_BONUS_CAP)
        xp = _BASE_SIGN_IN_XP + bonus
        return EventResult(
            event_type="sign_in", description=f"连续签到第 {new_streak} 天", xp=xp
        )
