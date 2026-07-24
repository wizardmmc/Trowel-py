"""Trowel session 与 Claude Code session 的持久绑定。"""

import sqlite3

from trowel_py.memory.sessions_repo import create_sessions_repository

from .support import repository, session_binding, session_record


def test_bind_and_find_cc_by_trowel() -> None:
    repo = repository()
    repo.bind_session(session_binding())
    binding = repo.find_cc_by_trowel("t1")
    assert binding is not None
    assert binding.cc_session_id == "cc-1"
    assert binding.session_kind == "user"
    assert binding.workdir == "/workspace/project"


def test_bind_many_trowel_to_one_cc() -> None:
    repo = repository()
    repo.bind_session(
        session_binding(
            trowel_session_id="t1",
            bound_at="t1",
        )
    )
    repo.bind_session(
        session_binding(
            trowel_session_id="t2",
            bound_at="t2",
        )
    )
    bindings = repo.find_trowels_by_cc("cc-1")
    assert {binding.trowel_session_id for binding in bindings} == {"t1", "t2"}
    by_id = {binding.trowel_session_id: binding for binding in bindings}
    assert by_id["t1"].bound_at == "t1"
    assert by_id["t2"].bound_at == "t2"


def test_bind_idempotent_on_trowel_id() -> None:
    repo = repository()
    repo.bind_session(session_binding())
    repo.bind_session(session_binding())
    assert len(repo.find_trowels_by_cc("cc-1")) == 1


def test_find_cc_by_trowel_missing() -> None:
    assert repository().find_cc_by_trowel("nope") is None


def test_find_trowels_by_cc_empty() -> None:
    assert repository().find_trowels_by_cc("nope") == []


def test_binding_kind_independent_of_sessions_row() -> None:
    repo = repository()
    repo.bind_session(
        session_binding(
            trowel_session_id="te",
            cc_session_id="cce",
            session_kind="eval",
        )
    )
    binding = repo.find_cc_by_trowel("te")
    assert binding is not None
    assert binding.session_kind == "eval"


def test_bindings_table_created_on_old_db(tmp_path) -> None:
    database = tmp_path / "sessions.db"
    first = sqlite3.connect(str(database))
    create_sessions_repository(first)
    first.close()

    second = sqlite3.connect(str(database))
    create_sessions_repository(second)
    columns = {
        row["name"] for row in second.execute("PRAGMA table_info(session_bindings)")
    }
    assert {
        "trowel_session_id",
        "cc_session_id",
        "session_kind",
        "workdir",
        "bound_at",
    } <= columns
    second.close()


def test_register_persists_trowel_binding() -> None:
    repo = repository()
    repo.register(
        session_record(
            cc_session_id="cc-x",
            trowel_session_id="t-x",
            session_kind="user",
        )
    )
    binding = repo.find_cc_by_trowel("t-x")
    assert binding is not None
    assert binding.cc_session_id == "cc-x"
    assert binding.session_kind == "user"


def test_register_without_trowel_id_skips_bind() -> None:
    repo = repository()
    repo.register(session_record(cc_session_id="cc-y"))
    assert repo.find_cc_by_trowel("anything") is None
    assert repo.find_trowels_by_cc("cc-y") == []
