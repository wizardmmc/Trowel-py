"""tests for access/outcome attribution resolution (slice-061)."""
from __future__ import annotations

import sqlite3

from trowel_py.memory.attribution import AttributionIndex
from trowel_py.memory.sessions_repo import (
    SessionBinding,
    SessionRecord,
    create_sessions_repository,
)


def _repo() -> object:
    return create_sessions_repository(sqlite3.connect(":memory:"))


def test_resolve_via_trowel_binding_when_cc_id_empty() -> None:
    """C-3: a record whose cc_session_id is empty (written pre-init) still
    resolves through the trowel binding."""
    repo = _repo()
    repo.bind_session(SessionBinding("t1", "cc-x", "user", "/w", "t"))
    idx = AttributionIndex.from_repo(repo)
    a = idx.resolve("t1", "")
    assert a.cc_session_id == "cc-x"
    assert a.session_kind == "user"
    assert a.basis == "trowel_binding"
    assert a.is_user and a.attributed


def test_resolve_falls_back_to_cc_session_id() -> None:
    """no binding, but a non-empty cc_session_id with a sessions row → resolve."""
    repo = _repo()
    repo.register(
        SessionRecord(
            cc_session_id="cc-y", workdir="/w", date="2026-07-17",
            registered_at="t", session_kind="review",
        )
    )
    idx = AttributionIndex.from_repo(repo)
    a = idx.resolve("", "cc-y")
    assert a.cc_session_id == "cc-y"
    assert a.session_kind == "review"
    assert a.basis == "cc_session_id"
    assert not a.is_user and a.attributed


def test_resolve_cc_id_unknown_kind() -> None:
    """a non-empty cc_session_id with NO sessions row is attributed but unknown."""
    a = AttributionIndex.from_repo(_repo()).resolve("", "cc-orphan")
    assert a.cc_session_id == "cc-orphan"
    assert a.session_kind == "unknown"
    assert a.basis == "cc_session_id"


def test_resolve_unattributed_when_both_empty() -> None:
    a = AttributionIndex.from_repo(_repo()).resolve("", "")
    assert a.cc_session_id is None
    assert a.basis == "unattributed"
    assert not a.attributed and not a.is_user


def test_trowel_binding_takes_precedence_over_cc_id() -> None:
    """when both resolve, the trowel binding wins (it is the primary key)."""
    repo = _repo()
    repo.bind_session(SessionBinding("t1", "cc-a", "user", "/w", "t"))
    idx = AttributionIndex.from_repo(repo)
    a = idx.resolve("t1", "cc-b")  # stale/wrong cc_session_id on the record
    assert a.cc_session_id == "cc-a"
    assert a.basis == "trowel_binding"


def test_many_trowel_ids_one_cc_all_resolve_to_it() -> None:
    """C-4: two trowel sessions resuming one cc id → both resolve to it."""
    repo = _repo()
    repo.bind_session(SessionBinding("t1", "cc-x", "user", "/w", "t1"))
    repo.bind_session(SessionBinding("t2", "cc-x", "user", "/w", "t2"))
    idx = AttributionIndex.from_repo(repo)
    assert idx.resolve("t1", "").cc_session_id == "cc-x"
    assert idx.resolve("t2", "").cc_session_id == "cc-x"


def test_from_root_missing_db_returns_empty_index(tmp_path) -> None:
    """a memory root with no sessions.db → an empty index (everything unresolved
    with a non-empty cc_id still attributes via cc_session_id)."""
    idx = AttributionIndex.from_root(tmp_path)
    assert idx.resolve("t1", "cc-x").basis == "cc_session_id"
    assert idx.resolve("", "").basis == "unattributed"
