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
    repo = _repo()
    repo.claude.bind_session(SessionBinding("t1", "cc-x", "user", "/w", "t"))
    idx = AttributionIndex.from_repo(repo)
    a = idx.resolve("t1", "")
    assert a.cc_session_id == "cc-x"
    assert a.session_kind == "user"
    assert a.basis == "trowel_binding"
    assert a.is_user and a.attributed


def test_resolve_falls_back_to_cc_session_id() -> None:
    repo = _repo()
    repo.claude.register(
        SessionRecord(
            cc_session_id="cc-y",
            workdir="/w",
            date="2026-07-17",
            registered_at="t",
            session_kind="review",
        )
    )
    idx = AttributionIndex.from_repo(repo)
    a = idx.resolve("", "cc-y")
    assert a.cc_session_id == "cc-y"
    assert a.session_kind == "review"
    assert a.basis == "cc_session_id"
    assert not a.is_user and a.attributed


def test_resolve_cc_id_unknown_kind() -> None:
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
    repo = _repo()
    repo.claude.bind_session(SessionBinding("t1", "cc-a", "user", "/w", "t"))
    idx = AttributionIndex.from_repo(repo)
    a = idx.resolve("t1", "cc-b")
    assert a.cc_session_id == "cc-a"
    assert a.basis == "trowel_binding"


def test_many_trowel_ids_one_cc_all_resolve_to_it() -> None:
    repo = _repo()
    repo.claude.bind_session(SessionBinding("t1", "cc-x", "user", "/w", "t1"))
    repo.claude.bind_session(SessionBinding("t2", "cc-x", "user", "/w", "t2"))
    idx = AttributionIndex.from_repo(repo)
    assert idx.resolve("t1", "").cc_session_id == "cc-x"
    assert idx.resolve("t2", "").cc_session_id == "cc-x"


def test_from_root_missing_db_returns_empty_index(tmp_path) -> None:
    idx = AttributionIndex.from_root(tmp_path)
    assert idx.resolve("t1", "cc-x").basis == "cc_session_id"
    assert idx.resolve("", "").basis == "unattributed"
    assert not (tmp_path / "meta" / "sessions.db").exists()


def test_from_root_read_only_does_not_migrate_old_database(tmp_path) -> None:
    """观察查询遇到旧 schema 时宁可不可用，也不能改写正式数据库。"""

    meta = tmp_path / "meta"
    meta.mkdir()
    database_path = meta / "sessions.db"
    connection = sqlite3.connect(database_path)
    connection.execute("CREATE TABLE legacy_only(value TEXT)")
    connection.commit()
    connection.close()

    index = AttributionIndex.from_root(tmp_path, read_only=True)

    assert index.resolve("", "").basis == "unattributed"
    check = sqlite3.connect(database_path)
    try:
        tables = {
            row[0]
            for row in check.execute(
                "SELECT name FROM sqlite_schema WHERE type='table'"
            )
        }
    finally:
        check.close()
    assert tables == {"legacy_only"}


def test_resolve_codex_access_via_trowel_session() -> None:
    """Codex turn 的 Trowel 身份必须进入与 CC 相同的用户归因入口。"""

    repo = _repo()
    repo.codex.register_turn(
        thread_id="thread-codex",
        turn_id="turn-codex",
        trowel_session_id="trowel-codex",
        workdir="/w",
        journal_path="/journal",
        registered_at="2026-08-03T10:00:00+08:00",
        model="gpt-5.6-sol",
        effort="xhigh",
        provider="openai",
        memory_enabled=True,
        profile_enabled=True,
        session_kind="user",
    )

    attribution = AttributionIndex.from_repo(repo).resolve(
        "trowel-codex",
        "",
        host_kind="codex",
        native_session_id="thread-codex",
    )

    assert attribution.cc_session_id == "thread-codex"
    assert attribution.session_kind == "user"
    assert attribution.is_user
