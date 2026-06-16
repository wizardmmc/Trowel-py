"""
event engine types: the value objects the pure logic layer operates on.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Literal

# the seven event types the engine can select
EventType = Literal[
    "sign_in",  # daily check-in
    "challenge",    # draw a question from due cards
    "discovery",    # find a new item
    "story",    # pet tells a story
    "growth",   # card-stage growth, celebrate your card into next stage
    "gift", # pet gives a gift
    "feynman",  # feynman mode
]

@dataclass(frozen=True)
class EventConfig:
    """
    static config for one event type

    Attributes:
        type: which event this config governs.
        weight: base probability weight; higher = picked more often
        cooldown_minutes: how long before thsi event can fire again
        min_cards: minmum total cards needed before this event is eligible
    """
    type: EventType
    weight: int
    cooldown_minutes: int
    min_cards: int

@dataclass(frozen=True)
class GameState:
    """
    the aggregate context the engine needs to pick an event

    intentionally holds only summary numbers, not raw card rows: the engine must not now where cards live

     Attributes:
        total_cards: how many cards the player has in total.
        due_cards: how many are due for review right now.
        player_level: current player level (>= 1).
        streak_days: current daily check-in streak.
        learned_card_ids: ids of cards reviewed at least once (tuple = immutable + hashable).
    """
    total_cards: int
    due_cards: int
    player_level: int
    streak_days: int
    learned_card_ids: tuple[str, ...] = ()