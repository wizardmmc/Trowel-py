"""
event service tests — the orchestration: trigger_event picks an off-cooldown
event, runs its handler, distributes rewards. covers the None paths too
(nothing eligible, handler declines).
"""
from __future__ import annotations

import random

from trowel_py.cards.repository import create_card_repository
from trowel_py.events.config import DEFAULT_EVENT_CONFIGS
from trowel_py.events.repository import create_event_repository
from trowel_py.events.service import trigger_event
from trowel_py.player.repository import create_player_repository
from trowel_py.review.repository import create_review_repository

from tests.events.conftest import NOW, seed_card


def _repos(db):
    return (
        create_player_repository(db),
        create_card_repository(db),
        create_review_repository(db),
        create_event_repository(db),
    )


def _cooldown_all_except(db, keep: str) -> None:
    ev = create_event_repository(db)
    for cfg in DEFAULT_EVENT_CONFIGS:
        if cfg.type != keep:
            ev.upsert_cooldown(cfg.type, NOW)


def test_picks_off_cooldown_event_and_grants(db):
    for i in range(3):
        seed_card(db, f"c{i}")
    _cooldown_all_except(db, "gift")  # only gift is eligible -> must pick it

    player_repo, card_repo, review_repo, ev = _repos(db)
    log = trigger_event(player_repo, card_repo, review_repo, ev, NOW, random.Random(0))

    assert log is not None
    assert log.event_type == "gift"
    assert log.reward_xp == 10
    # side effects actually happened
    assert len(player_repo.find_inventory()) == 1
    assert "gift" in ev.get_last_triggered_map()


def test_returns_none_when_all_on_cooldown(db):
    for i in range(3):
        seed_card(db, f"c{i}")
    _cooldown_all_except(db, "__none__")  # cooldown literally everything

    player_repo, card_repo, review_repo, ev = _repos(db)
    assert trigger_event(player_repo, card_repo, review_repo, ev, NOW, random.Random(0)) is None


def test_returns_none_when_handler_declines(db):
    # 3 cards -> feynman min_cards=3 met; cooldown all except feynman -> picks feynman,
    # but feynman.can_trigger() is False -> None
    for i in range(3):
        seed_card(db, f"c{i}")
    _cooldown_all_except(db, "feynman")

    player_repo, card_repo, review_repo, ev = _repos(db)
    assert trigger_event(player_repo, card_repo, review_repo, ev, NOW, random.Random(0)) is None
