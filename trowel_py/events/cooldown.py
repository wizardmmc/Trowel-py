"""按时间窗口和卡片数量过滤事件。"""

from __future__ import annotations

from datetime import datetime

from trowel_py.events.types import EventConfig, EventType, GameState

Cooldowns = dict[EventType, datetime]

_SECONDS_PER_MINUTE = 60


def is_on_cooldown(
    event_type: EventType,
    cooldowns: Cooldowns,
    cooldown_minutes: int,
    now: datetime,
) -> bool:
    """未来触发时间仍视为冷却中；到达窗口边界即解除。"""
    last_triggered = cooldowns.get(event_type)
    if last_triggered is None:
        return False
    elapsed_seconds = (now - last_triggered).total_seconds()
    return int(elapsed_seconds) < cooldown_minutes * _SECONDS_PER_MINUTE


def filter_eligible(
    configs: tuple[EventConfig, ...],
    state: GameState,
    cooldowns: Cooldowns,
    now: datetime,
) -> tuple[EventConfig, ...]:
    """过滤结果保持输入配置的顺序。"""
    eligible = [
        c
        for c in configs
        if state.total_cards >= c.min_cards
        and not is_on_cooldown(c.type, cooldowns, c.cooldown_minutes, now)
    ]
    return tuple(eligible)
