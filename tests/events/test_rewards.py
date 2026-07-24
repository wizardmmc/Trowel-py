from __future__ import annotations

from trowel_py.events.handlers.types import EventResult
from trowel_py.events.repository import create_event_repository
from trowel_py.events.rewards import distribute
from trowel_py.player.repository import create_player_repository

from tests.events.conftest import NOW


def test_grants_xp_coins_and_logs(db):
    repo = create_player_repository(db)
    ev = create_event_repository(db)
    result = EventResult(
        event_type="challenge", description="q", xp=30, coins=15, card_id="c1"
    )

    log = distribute(result, repo, ev, NOW)

    player = repo.find_or_create()
    assert player.xp == 30
    assert player.coins == 15
    assert log.event_type == "challenge"
    assert log.reward_xp == 30
    assert any(item.id == log.id for item in ev.get_recent(5))
    assert "challenge" in ev.get_last_triggered_map()


def test_grants_item_with_inferred_type(db):
    repo = create_player_repository(db)
    ev = create_event_repository(db)
    result = EventResult(
        event_type="discovery", description="d", xp=10, item_id="hat_straw"
    )

    distribute(result, repo, ev, NOW)

    inv = repo.find_inventory()
    assert len(inv) == 1
    assert inv[0].item_id == "hat_straw"
    assert inv[0].item_type == "hat"


def test_skips_zero_rewards_but_still_logs(db):
    repo = create_player_repository(db)
    ev = create_event_repository(db)
    result = EventResult(event_type="feynman", description="d", xp=0, coins=0)

    distribute(result, repo, ev, NOW)

    assert repo.find_or_create().xp == 0
    assert repo.find_or_create().coins == 0
    assert repo.find_inventory() == []
    assert len(ev.get_recent(5)) == 1


def test_no_item_when_item_id_none(db):
    repo = create_player_repository(db)
    ev = create_event_repository(db)
    result = EventResult(event_type="sign_in", description="d", xp=20)

    distribute(result, repo, ev, NOW)

    assert repo.find_inventory() == []
