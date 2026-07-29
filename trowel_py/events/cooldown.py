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
    """判断指定事件是否仍在冷却时间内；到达时间边界即解除。"""
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
    """按卡片数量和冷却时间过滤可触发事件，并保持输入顺序。"""
    eligible = [
        c
        for c in configs
        if state.total_cards >= c.min_cards
        and not is_on_cooldown(c.type, cooldowns, c.cooldown_minutes, now)
    ]
    return tuple(eligible)
