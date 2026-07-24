from __future__ import annotations

from datetime import timedelta

from trowel_py.events.repository import create_event_repository

from tests.events.conftest import NOW, seed_card


def test_record_event_inserts_and_returns(db):
    ev = create_event_repository(db)
    log = ev.record_event("sign_in", "daily check-in", 20, 0, None, None, NOW)

    assert log.event_type == "sign_in"
    assert log.reward_xp == 20
    assert log.player_id == "default"

    recent = ev.get_recent(5)
    assert len(recent) == 1
    assert recent[0].id == log.id


def test_record_event_preserves_all_fields_in_model_and_row(db):
    seed_card(db, "card-1")
    ev = create_event_repository(db)

    log = ev.record_event(
        "challenge",
        "explain recursion",
        30,
        15,
        "hat_straw",
        "card-1",
        NOW,
    )

    assert log.model_dump() == {
        "id": log.id,
        "player_id": "default",
        "event_type": "challenge",
        "reward_xp": 30,
        "reward_coin": 15,
        "reward_item_id": "hat_straw",
        "description": "explain recursion",
        "card_id": "card-1",
        "triggered_at": NOW,
    }
    assert len(log.id) == 12
    row = db.execute("select * from event_log where id = ?", (log.id,)).fetchone()
    assert dict(row) == {
        "id": log.id,
        "player_id": "default",
        "event_type": "challenge",
        "reward_xp": 30,
        "reward_coin": 15,
        "reward_item_id": "hat_straw",
        "triggered_at": NOW.isoformat(),
        "description": "explain recursion",
        "card_id": "card-1",
    }


def test_get_recent_orders_newest_first(db):
    ev = create_event_repository(db)
    ev.record_event("sign_in", "a", 0, 0, None, None, NOW)
    ev.record_event("gift", "b", 0, 0, None, None, NOW + timedelta(hours=1))

    types = [log.event_type for log in ev.get_recent(5)]
    assert types == ["gift", "sign_in"]


def test_get_recent_respects_limit(db):
    ev = create_event_repository(db)
    for i in range(5):
        ev.record_event("sign_in", str(i), 0, 0, None, None, NOW + timedelta(hours=i))

    assert len(ev.get_recent(2)) == 2


def test_get_recent_excludes_no_card_when_filtering_ids(db):
    ev = create_event_repository(db)
    ev.record_event("sign_in", "no card", 0, 0, None, None, NOW)
    ev.record_event("challenge", "has card", 0, 0, None, "c1", NOW)

    assert len(ev.get_recent(5)) == 2
    assert ev.get_recent_card_ids("challenge", 5) == ["c1"]
    assert ev.get_recent_card_ids("sign_in", 5) == []


def test_cooldown_map_empty_initially(db):
    ev = create_event_repository(db)
    assert ev.get_last_triggered_map() == {}


def test_upsert_cooldown_inserts_then_updates(db):
    ev = create_event_repository(db)

    ev.upsert_cooldown("sign_in", NOW)
    assert ev.get_last_triggered_map() == {"sign_in": NOW}

    later = NOW + timedelta(hours=1)
    ev.upsert_cooldown("sign_in", later)
    mapping = ev.get_last_triggered_map()
    assert mapping == {"sign_in": later}
