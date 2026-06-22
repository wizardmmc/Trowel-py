"""
pure-logic types
"""
from __future__ import annotations
from typing import Literal

PetMood = Literal["happy", "excited", "curious", "normal"]

MoodTrigger = Literal[
    "review_correct",   # answer a card right -> happy
    "review_complete",  # finished a review session -> excited
    "event_trigger",    # a random event fired -> excited
    "interaction",  # petted -> happy
    "hunger_low",   # hunger dropped low -> normal (no punishment)
    "idle", # nothing happened -> normal
    "feynman_trigger"    # entered feyman mode -> curious
]