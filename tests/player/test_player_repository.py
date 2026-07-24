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
    conn.execute(
        "insert into players (id, last_active, xp, coins, streak_days) "
        "values ('default', ?, ?, ?, ?)",
        (last_active, xp, coins, streak_days),
    )


def _seed_inventory(
    conn: sqlite3.Connection,
    row_id: str,
    item_id: str,
    item_type: str,
    *,
    player_id: str = "default",
    equipped: int = 0,
    obtained_at: str = "2026-06-15T11:00:00",
) -> None:
    conn.execute(
        "insert into inventory "
        "(id, player_id, item_id, item_type, equipped, obtained_at) "
        "values (?, ?, ?, ?, ?, ?)",
        (row_id, player_id, item_id, item_type, equipped, obtained_at),
    )


def test_find_or_create_inserts_on_first_call(db_connection: sqlite3.Connection):
    run_migrations(db_connection)
    repo = create_player_repository(db_connection)

    player = repo.find_or_create()

    assert player.id == "default"
    assert player.xp == 0
    assert player.coins == 0
    assert player.streak_days == 0
    assert isinstance(player.last_active, datetime)


def test_find_or_create_is_idempotent(db_connection: sqlite3.Connection):
    run_migrations(db_connection)
    repo = create_player_repository(db_connection)

    repo.find_or_create()
    repo.find_or_create()

    count = db_connection.execute("select count(*) as c from players").fetchone()["c"]
    assert count == 1


def test_find_or_create_returns_existing(db_connection: sqlite3.Connection):
    run_migrations(db_connection)
    _seed_player(db_connection, xp=150, coins=200, streak_days=3)

    repo = create_player_repository(db_connection)
    player = repo.find_or_create()

    assert player.xp == 150
    assert player.coins == 200
    assert player.streak_days == 3


def test_find_or_create_decodes_all_player_fields(
    db_connection: sqlite3.Connection,
):
    run_migrations(db_connection)
    _seed_player(
        db_connection,
        last_active="2026-06-15T10:00:00",
        xp=150,
        coins=200,
        streak_days=3,
    )
    db_connection.execute(
        "update players set created_at = ? where id = 'default'",
        ("2026-06-01T09:00:00",),
    )

    player = create_player_repository(db_connection).find_or_create()

    assert player.model_dump() == {
        "id": "default",
        "xp": 150,
        "coins": 200,
        "streak_days": 3,
        "last_active": datetime(2026, 6, 15, 10, 0),
        "created_at": datetime(2026, 6, 1, 9, 0),
    }


def test_update_xp_increments(db_connection: sqlite3.Connection):
    run_migrations(db_connection)
    _seed_player(db_connection, xp=100)
    repo = create_player_repository(db_connection)

    repo.update_xp(50)

    xp = db_connection.execute("select xp from players where id='default'").fetchone()[
        "xp"
    ]
    assert xp == 150


def test_update_xp_negative_subtracts(db_connection: sqlite3.Connection):
    run_migrations(db_connection)
    _seed_player(db_connection, xp=100)
    repo = create_player_repository(db_connection)

    repo.update_xp(-30)

    xp = db_connection.execute("select xp from players where id='default'").fetchone()[
        "xp"
    ]
    assert xp == 70


def test_update_coins_spend(db_connection: sqlite3.Connection):
    run_migrations(db_connection)
    _seed_player(db_connection, coins=100)
    repo = create_player_repository(db_connection)

    repo.update_coins(-40)

    coins = db_connection.execute(
        "select coins from players where id='default'"
    ).fetchone()["coins"]
    assert coins == 60


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
    assert len(items[0].id) == 12


def test_find_inventory_decodes_all_fields(db_connection: sqlite3.Connection):
    run_migrations(db_connection)
    _seed_player(db_connection)
    _seed_inventory(
        db_connection,
        "row-1",
        "hat_straw",
        "hat",
        equipped=1,
    )
    repo = create_player_repository(db_connection)

    items = repo.find_inventory()

    assert [item.model_dump() for item in items] == [
        {
            "id": "row-1",
            "player_id": "default",
            "item_id": "hat_straw",
            "item_type": "hat",
            "equipped": 1,
            "obtained_at": datetime(2026, 6, 15, 11, 0),
        }
    ]


def test_find_item_by_id_requires_default_player_ownership(
    db_connection: sqlite3.Connection,
):
    run_migrations(db_connection)
    _seed_player(db_connection)
    db_connection.execute(
        "insert into players (id, last_active) values (?, ?)",
        ("other", "2026-06-15T10:00:00"),
    )
    _seed_inventory(
        db_connection,
        "other-row",
        "hat_straw",
        "hat",
        player_id="other",
    )
    repo = create_player_repository(db_connection)

    assert repo.find_item_by_id("other-row") is None


def test_set_equipped_updates_requested_row(db_connection: sqlite3.Connection):
    run_migrations(db_connection)
    _seed_player(db_connection)
    _seed_inventory(db_connection, "hat-1", "hat_straw", "hat")
    repo = create_player_repository(db_connection)

    repo.set_equipped("hat-1", 1)

    assert repo.find_item_by_id("hat-1").equipped == 1


def test_unequip_all_hats_keeps_food_state(db_connection: sqlite3.Connection):
    run_migrations(db_connection)
    _seed_player(db_connection)
    _seed_inventory(
        db_connection,
        "hat-1",
        "hat_straw",
        "hat",
        equipped=1,
    )
    _seed_inventory(
        db_connection,
        "food-1",
        "food_basic",
        "food",
        equipped=1,
    )
    repo = create_player_repository(db_connection)

    repo.unequip_all_hats()

    assert repo.find_item_by_id("hat-1").equipped == 0
    assert repo.find_item_by_id("food-1").equipped == 1


def test_remove_item(db_connection: sqlite3.Connection):
    run_migrations(db_connection)
    _seed_player(db_connection)
    repo = create_player_repository(db_connection)

    repo.add_item("food_basic", "food")
    item_id = repo.find_inventory()[0].id

    repo.remove_item(item_id)
    assert repo.find_inventory() == []
