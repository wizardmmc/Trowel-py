import sqlite3
from datetime import datetime

import pytest

from trowel_py.db.migrate import run_migrations
from trowel_py.player.repository import create_player_repository
from trowel_py.player.service import (
    add_coins,
    add_xp,
    calculate_level,
    get_profile,
    spend_coins,
    update_streak,
    xp_to_next_level,
)


def _seed_player(
    conn: sqlite3.Connection,
    last_active: str = "2026-06-15T10:00:00",
    xp: int = 0,
    coins: int = 0,
    streak_days: int = 0,
) -> None:
    conn.execute(
        "insert into players (id, last_active, xp, coins, streak_days) "
        "values ('default', ?, ?, ?, ?)",
        (last_active, xp, coins, streak_days),
    )


@pytest.mark.parametrize(
    "total_xp, expected",
    [
        (0, 1),
        (99, 1),
        (100, 2),
        (101, 2),
        (299, 2),
        (300, 3),
        (599, 3),
        (600, 4),
        (1000, 5),
    ],
)
def test_calculate_level_boundaries(total_xp: int, expected: int):
    assert calculate_level(total_xp) == expected


def test_xp_to_next_level_at_start():
    assert xp_to_next_level(0, 1) == 100


def test_xp_to_next_level_midway():
    assert xp_to_next_level(150, 2) == 150


def test_xp_to_next_level_just_below():
    assert xp_to_next_level(99, 1) == 1


def test_get_profile_computes_level(db_connection: sqlite3.Connection):
    run_migrations(db_connection)
    _seed_player(db_connection, xp=150, coins=200)
    repo = create_player_repository(db_connection)

    profile = get_profile(repo)

    assert profile.level == 2
    assert profile.xp_to_next_level == 150
    assert profile.coins == 200
    assert profile.xp == 150


def test_add_xp_crosses_level_up(db_connection: sqlite3.Connection):
    run_migrations(db_connection)
    _seed_player(db_connection, xp=0)
    repo = create_player_repository(db_connection)

    assert add_xp(100, repo) == 2


def test_add_xp_no_level_up(db_connection: sqlite3.Connection):
    run_migrations(db_connection)
    _seed_player(db_connection, xp=0)
    repo = create_player_repository(db_connection)

    assert add_xp(50, repo) == 1


def test_add_coins(db_connection: sqlite3.Connection):
    run_migrations(db_connection)
    _seed_player(db_connection, coins=10)
    repo = create_player_repository(db_connection)

    add_coins(5, repo)

    assert repo.find_or_create().coins == 15


def test_update_streak_same_day(db_connection: sqlite3.Connection):
    run_migrations(db_connection)
    _seed_player(db_connection, last_active="2026-06-15T08:00:00", streak_days=3)
    repo = create_player_repository(db_connection)

    now = datetime(2026, 6, 15, 20, 0, 0)

    assert update_streak(repo, now) == 3


def test_update_streak_consecutive(db_connection: sqlite3.Connection):
    run_migrations(db_connection)
    _seed_player(db_connection, last_active="2026-06-14T08:00:00", streak_days=3)
    repo = create_player_repository(db_connection)

    now = datetime(2026, 6, 15, 10, 0, 0)

    assert update_streak(repo, now) == 4


def test_update_streak_broken(db_connection: sqlite3.Connection):
    run_migrations(db_connection)
    _seed_player(db_connection, last_active="2026-06-12T08:00:00", streak_days=5)
    repo = create_player_repository(db_connection)

    now = datetime(2026, 6, 15, 10, 0, 0)

    assert update_streak(repo, now) == 1


def test_update_streak_persists(db_connection: sqlite3.Connection):
    run_migrations(db_connection)
    _seed_player(db_connection, last_active="2026-06-14T08:00:00", streak_days=3)
    repo = create_player_repository(db_connection)

    update_streak(repo, datetime(2026, 6, 15, 10, 0, 0))

    row = db_connection.execute(
        "select streak_days from players where id='default'"
    ).fetchone()
    assert row["streak_days"] == 4


def test_spend_coins_success_deducts_and_grants(db_connection: sqlite3.Connection):
    run_migrations(db_connection)
    _seed_player(db_connection, coins=100)
    repo = create_player_repository(db_connection)

    item_type = spend_coins("food_basic", repo)

    assert item_type == "food"
    assert repo.find_or_create().coins == 90
    assert len(repo.find_inventory()) == 1


def test_spend_coins_hat_type(db_connection: sqlite3.Connection):
    run_migrations(db_connection)
    _seed_player(db_connection, coins=100)
    repo = create_player_repository(db_connection)

    assert spend_coins("hat_straw", repo) == "hat"


def test_spend_coins_not_enough_is_atomic(db_connection: sqlite3.Connection):
    run_migrations(db_connection)
    _seed_player(db_connection, coins=5)
    repo = create_player_repository(db_connection)

    with pytest.raises(ValueError):
        spend_coins("hat_straw", repo)

    assert repo.find_or_create().coins == 5
    assert repo.find_inventory() == []


def test_spend_coins_unknown_item(db_connection: sqlite3.Connection):
    run_migrations(db_connection)
    _seed_player(db_connection, coins=1000)
    repo = create_player_repository(db_connection)

    with pytest.raises(ValueError):
        spend_coins("banana", repo)
