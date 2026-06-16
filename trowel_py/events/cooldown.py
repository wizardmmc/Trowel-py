"""
cooldown filtering: drop events that fired too recently or lack the cards to be meaningful.
"""
from __future__ import annotations
from datetime import datetime
from trowel_py.events.types import EventConfig, EventType, GameState

# map an event type to the moment it last fired
Cooldowns = dict[EventType, datetime]

_SECONDS_PER_MINUTE = 60

def is_on_cooldown(
    event_type: EventType,
    cooldowns: Cooldowns,
    cooldown_minutes: int,
    now: datetime
) -> bool:
    """
    is 'event_type' still inside its cooldown window?

    Args:
        event_type: the event to check
        cooldowns: map of event type -> last-triggered time
        cooldown_minutes: window length in minutes
        now: the current moment (injected fot testability)

    Returns:
        True if the event fired within the last 'cooldown_minutes'
    """
    last_triggered = cooldowns.get(event_type)
    if last_triggered is None:
        return False
    elapsed_seconds = (now - last_triggered).total_seconds()
    return int(elapsed_seconds) < cooldown_minutes * _SECONDS_PER_MINUTE

def filter_eligible(
    configs: tuple[EventConfig, ...],
    state: GameState,
    cooldowns: Cooldowns,
    now: datetime
) -> tuple[EventConfig, ...]:
    """
    keep only configs that are off cooldown and meet the card minimum

    Args:
        configs: the full set of event configs to filter
        state: current aggregate game state
        cooldowns: map of event type -> last-triggered time
        now: the current moment
    """
    eligible = [
        c for c in configs
        if state.total_cards >= c.min_cards
        and not is_on_cooldown(c.type, cooldowns, c.cooldown_minutes, now)
    ]
    return tuple(eligible)
