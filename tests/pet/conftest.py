from __future__ import annotations

import pytest

from trowel_py.db.migrate import run_migrations
from trowel_py.pet.repository import create_pet_repository
from trowel_py.player.repository import create_player_repository


@pytest.fixture
def pet_db(db_connection):
    run_migrations(db_connection)
    create_player_repository(db_connection).find_or_create()
    return db_connection


@pytest.fixture
def pet_repo(pet_db):
    return create_pet_repository(pet_db)


@pytest.fixture
def player_repo(pet_db):
    return create_player_repository(pet_db)


@pytest.fixture
def stock_item(player_repo):
    def _stock(catalog_id: str, item_type: str) -> str:
        existing_ids = {item.id for item in player_repo.find_inventory()}
        player_repo.add_item(catalog_id, item_type)
        return next(
            item.id
            for item in player_repo.find_inventory()
            if item.id not in existing_ids
        )

    return _stock
