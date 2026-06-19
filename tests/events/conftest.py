"""
shared fixtures for event tests.

`db` builds a migrated in-memory database with the default player seeded
(event_log.player_id FK needs that row). helpers seed cards / fsrs state.
everything stays on one connection, so writes are visible without commit.
"""
from __future__ import annotations

import random
from datetime import datetime
from typing import Sequence

import pytest

from trowel_py.db.migrate import run_migrations
from trowel_py.cards.repository import create_card_repository
from trowel_py.player.repository import create_player_repository
from trowel_py.review.repository import create_review_repository
from trowel_py.events.repository import create_event_repository
from trowel_py.events.handlers.types import EventDependencies
from trowel_py.schemas.card import Card
from trowel_py.schemas.review import FSRSState

NOW = datetime(2026, 6, 19, 10, 0, 0)
FAR_FUTURE = datetime(2099, 1, 1, 0, 0, 0)  # default due: not due


@pytest.fixture
def db(db_connection):
    """migrated db + default player row (satisfies event_log.player_id FK)."""
    run_migrations(db_connection)
    create_player_repository(db_connection).find_or_create()
    return db_connection


class FakeRng:
    """test double for random.Random: fixed random() value + controllable choice()."""

    def __init__(self, rand_value: float = 0.5, choice_index: int = 0) -> None:
        self._rand = rand_value
        self._choice_index = choice_index

    def random(self) -> float:
        return self._rand

    def choice(self, seq: Sequence):
        return seq[self._choice_index]


def make_deps(conn, now: datetime = NOW, rng=None):
    """build EventDependencies bound to one connection (atomicity preserved)."""
    return EventDependencies(
        player_repo=create_player_repository(conn),
        review_repo=create_review_repository(conn),
        card_repo=create_card_repository(conn),
        garden_repo=None,
        event_repo=create_event_repository(conn),
        now=now,
        rng=rng if rng is not None else random.Random(0),
    )


def seed_card(conn, card_id: str, title: str | None = None, explanation: str | None = None) -> Card:
    """insert one card; returns it."""
    card = Card(
        id=card_id,
        title=title or card_id,
        category="x",
        explanation=explanation or "explanation long enough to pass",
    )
    create_card_repository(conn).create(card)
    return card


def seed_state(conn, card_id: str, reps: int = 0, lapses: int = 0, due: datetime | None = None) -> FSRSState:
    """insert fsrs_state for a card; due defaults to far future (not due)."""
    fsrs = FSRSState(
        card_id=card_id,
        stability=1.0,
        difficulty=5.0,
        elapsed_days=1,
        scheduled_days=1,
        reps=reps,
        lapses=lapses,
        state=1,
        due=due or FAR_FUTURE,
        last_review=NOW,
    )
    create_review_repository(conn).save_fsrs_state(fsrs)
    return fsrs
