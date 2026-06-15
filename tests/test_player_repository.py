import sqlite3
from datetime import datetime

from trowel_py.db.migrate import run_migrations
from trowel_py.player.repository import create_player_repository


def _seed_player(
    conn: sqlite3.Connection,
    last_active: str = "2026-06-15T10:00:00",
    xp: int = 0,
    coins: int = 0,
    streak_days: int = 0,
) -> None:
    """helper: insert the default player with a known state."""
    conn.execute(
        "insert into players (id, last_active, xp, coins, streak_days) "
        "values ('default', ?, ?, ?, ?)",
        (last_active, xp, coins, streak_days),
    )


# ---- find_or_create ----

def test_find_or_create_inserts_on_first_call(db_connection: sqlite3.Connection):
    """empty DB -> find_or_create creates the default player with defaults."""
    run_migrations(db_connection)
    repo = create_player_repository(db_connection)

    player = repo.find_or_create()

    assert player.id == "default"
    assert player.xp == 0
    assert player.coins == 0
    assert player.streak_days == 0
    assert isinstance(player.last_active, datetime)


def test_find_or_create_is_idempotent(db_connection: sqlite3.Connection):
    """calling twice must NOT create a second row."""
    run_migrations(db_connection)
    repo = create_player_repository(db_connection)

    repo.find_or_create()
    repo.find_or_create()

    count = db_connection.execute("select count(*) as c from players").fetchone()["c"]
    assert count == 1


def test_find_or_create_returns_existing(db_connection: sqlite3.Connection):
    """if a player already exists, find_or_create returns it as-is."""
    run_migrations(db_connection)
    _seed_player(db_connection, xp=150, coins=200, streak_days=3)

    repo = create_player_repository(db_connection)
    player = repo.find_or_create()

    assert player.xp == 150
    assert player.coins == 200
    assert player.streak_days == 3


# ---- update_xp / update_coins (increment in DB) ----

def test_update_xp_increments(db_connection: sqlite3.Connection):
    run_migrations(db_connection)
    _seed_player(db_connection, xp=100)
    repo = create_player_repository(db_connection)

    repo.update_xp(50)

    xp = db_connection.execute("select xp from players where id='default'").fetchone()["xp"]
    assert xp == 150


def test_update_xp_negative_subtracts(db_connection: sqlite3.Connection):
    run_migrations(db_connection)
    _seed_player(db_connection, xp=100)
    repo = create_player_repository(db_connection)

    repo.update_xp(-30)

    xp = db_connection.execute("select xp from players where id='default'").fetchone()["xp"]
    assert xp == 70


def test_update_coins_spend(db_connection: sqlite3.Connection):
    run_migrations(db_connection)
    _seed_player(db_connection, coins=100)
    repo = create_player_repository(db_connection)

    repo.update_coins(-40)   # spend

    coins = db_connection.execute("select coins from players where id='default'").fetchone()["coins"]
    assert coins == 60


# ---- update_streak (repo just persists what the service computed) ----

def test_update_streak_persists(db_connection: sqlite3.Connection):
    run_migrations(db_connection)
    _seed_player(db_connection)
    repo = create_player_repository(db_connection)

    repo.update_streak(streak_days=5, last_active=datetime(2026, 6, 15, 12, 0, 0))

    row = db_connection.execute(
        "select streak_days, last_active from players where id='default'"
    ).fetchone()
    assert row["streak_days"] == 5
    assert row["last_active"] == "2026-06-15T12:00:00"


# ---- inventory ----

def test_find_inventory_empty(db_connection: sqlite3.Connection):
    run_migrations(db_connection)
    _seed_player(db_connection)
    repo = create_player_repository(db_connection)

    assert repo.find_inventory() == []


def test_add_item_then_find(db_connection: sqlite3.Connection):
    run_migrations(db_connection)
    _seed_player(db_connection)
    repo = create_player_repository(db_connection)

    repo.add_item("food_basic", "food")

    items = repo.find_inventory()
    assert len(items) == 1
    assert items[0].item_id == "food_basic"
    assert items[0].item_type == "food"
    assert items[0].player_id == "default"


def test_remove_item(db_connection: sqlite3.Connection):
    run_migrations(db_connection)
    _seed_player(db_connection)
    repo = create_player_repository(db_connection)

    repo.add_item("food_basic", "food")
    item_id = repo.find_inventory()[0].id

    repo.remove_item(item_id)
    assert repo.find_inventory() == []
