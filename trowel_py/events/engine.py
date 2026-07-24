"""事件资格过滤、权重调整与随机选择。"""

from __future__ import annotations

import random
from datetime import datetime

from trowel_py.events.cooldown import Cooldowns, filter_eligible
from trowel_py.events.types import EventConfig, EventType, GameState

WeightedItem = tuple[EventConfig, float]


def select_event(
    state: GameState,
    configs: tuple[EventConfig, ...],
    cooldowns: Cooldowns,
    now: datetime,
    rng: random.Random | None = None,
) -> EventType | None:
    eligible = filter_eligible(configs, state, cooldowns, now)
    if not eligible:
        return None

    weighted = _adjust_weights(eligible, state)
    rand = rng.random() if rng is not None else random.random()
    return _weighted_random(weighted, rand)


def _adjust_weights(
    configs: tuple[EventConfig, ...], state: GameState
) -> tuple[WeightedItem, ...]:
    boosted: list[WeightedItem] = []
    for c in configs:
        if c.type == "challenge":
            effective = c.weight * (1 + state.total_cards / 100)
        else:
            effective = float(c.weight)
        boosted.append((c, effective))
    return tuple(boosted)


def _weighted_random(items: tuple[WeightedItem, ...], rand: float) -> EventType:
    """`items` 必须非空且权重为正，`rand` 是预抽取的 `[0, 1)` 随机值。"""
    total_weight = sum(weight for _, weight in items)
    remaining = rand * total_weight
    for config, weight in items:
        remaining -= weight
        if remaining <= 0:
            return config.type
    # 浮点尾差可能让最后一次减法后仍略大于零，此时回退到末项。
    return items[-1][0].type
