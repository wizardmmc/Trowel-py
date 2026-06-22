"""shared fixtures for pet unit tests (repository + service).

`db` = migrated in-memory db + default player (pets.player_id FK needs it).
repos bind to the same conn so writes are visible without commit.

NOTE: routes tests (test_routes.py) use the REAL db + _clean_db instead — a
different isolation strategy, matching test_player_routes / test_garden_routes.
"""
from __future__ import annotations

import pytest

from trowel_py.db.migrate import run_migrations
from trowel_py.player.repository import create_player_repository
from trowel_py.pet.repository import create_pet_repository


@pytest.fixture
def db(db_connection):
    """migrated in-memory db + default player row (satisfies pets.player_id FK)."""
    run_migrations(db_connection)
    create_player_repository(db_connection).find_or_create()
    return db_connection


@pytest.fixture
def pet_repo(db):
    return create_pet_repository(db)


@pytest.fixture
def player_repo(db):
    return create_player_repository(db)


@pytest.fixture
def stock(player_repo):
    """return a fn that adds one inventory item and returns its row id.

    uses an id-diff (not list[-1]) so it stays correct even if sqlite returns
    rows in a non-insertion order.
    """
    def _stock(catalog: str, item_type: str) -> str:
        before = {item.id for item in player_repo.find_inventory()}
        player_repo.add_item(catalog, item_type)
        new = [item for item in player_repo.find_inventory() if item.id not in before]
        return new[0].id

    return _stock
