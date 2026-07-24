from __future__ import annotations
from trowel_py.events.types import EventConfig

DEFAULT_EVENT_CONFIGS: tuple[EventConfig, ...] = (
    EventConfig(type="sign_in", weight=100, cooldown_minutes=1440, min_cards=0),
    EventConfig(type="challenge", weight=40, cooldown_minutes=60, min_cards=3),
    EventConfig(type="discovery", weight=20, cooldown_minutes=120, min_cards=0),
    EventConfig(type="story", weight=15, cooldown_minutes=180, min_cards=5),
    EventConfig(type="growth", weight=10, cooldown_minutes=240, min_cards=3),
    EventConfig(type="gift", weight=15, cooldown_minutes=180, min_cards=0),
    EventConfig(type="feynman", weight=20, cooldown_minutes=120, min_cards=3),
)
