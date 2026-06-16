"""
the event engine core: pick which event type fires right now
"""
from __future__ import annotations

import random
from datetime import datetime

from trowel_py.events.cooldown import Cooldowns, filter_eligible
from trowel_py.events.types import EventConfig, EventType, GameState

# a config paired with its effective weight
WeightedItem = tuple[EventConfig, float]

def select_event(
    state: GameState,
    configs: tuple[EventConfig, ...],
    cooldowns: Cooldowns,
    now: datetime,
    rng: random.Random | None = None,
) -> EventType | None:
    """
    choose one event type to fire, or None if nothing is eligible

    Args:
        state: current aggregate game state
        configs: the full set of event
        cooldowns: map of event type -> last-triggered time
        now: the current moment
        rng: optional random source; defaults to the global random

    Returns:
        the chosen event type, or None when no config is eligible
    """
    eligible = filter_eligible(configs, state, cooldowns, now)
    if not eligible:
        return None
    
    weighted = _adjust_weights(eligible, state)
    rand = rng.random() if rng is not None else random.random()
    return _weighted_random(weighted, rand)

def _adjust_weights(
    configs: tuple[EventConfig, ...],
    state: GameState
) -> tuple[WeightedItem, ...]:
    """
    apply runtime weight modifiers

    challenge scales with garden size: the more cards a player owns, the more
    often challenges surface. other types keep their base weight

    Returns:
        each config paired with its effective weight
    """
    boosted: list[WeightedItem] = []
    for c in configs:
        if c.type == "challenge":
            effective = c.weight * (1 + state.total_cards / 100)
        else:
            effective = float(c.weight)
        boosted.append((c, effective))
    return tuple(boosted)

def _weighted_random(
    items: tuple[WeightedItem, ...],
    rand: float
) -> EventType:
    """
    pick one item with probability proportional to its weight

    Args:
        items: (config, effective_weight) pairs; at least one, all weights > 0
        rand: a pre-drawn uniform value in [0, 1)

    Returns:
        the chosen event type
    """
    total_weight = sum(weight for _, weight in items)
    remaining = rand * total_weight
    for config, weight in items:
        remaining -= weight
        if remaining <= 0:
            return config.type
    # float rounding can leave 'remaining' a hair above 0 after the last time
    # fall back to the final item rather than crashing
    return items[-1][0].type

