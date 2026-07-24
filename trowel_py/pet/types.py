"""宠物心情及其触发来源的稳定值域。"""

from __future__ import annotations

from typing import Literal

PetMood = Literal["happy", "excited", "curious", "normal"]

MoodTrigger = Literal[
    "review_correct",
    "review_complete",
    "event_trigger",
    "interaction",
    "hunger_low",
    "idle",
    "feynman_trigger",
]
