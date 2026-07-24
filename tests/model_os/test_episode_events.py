"""Episode 事件的 ownership 归因、完整幂等身份与 schema 迁移测试。"""

from __future__ import annotations

import pytest

from trowel_py.model_os.store import ModelOsStore
from trowel_py.model_os.types import EventEnvelope, EventKind, Provenance

from tests.model_os._episode_helpers import (
    FakeClock,
    activate_episode,
    make_running_system_episode,
)


def test_fenced_event_persists_lease_triple(store: ModelOsStore, monkeypatch) -> None:
    clock = FakeClock()
    clock.install(monkeypatch)
    episode, lease, _ = make_running_system_episode(store)
    activate_episode(store, episode.episode_id, lease)  # 受 fencing 保护的状态变更

    fenced = [
        ev
        for _, ev in store.list_events()
        if ev.kind == EventKind.EPISODE_STATUS_CHANGED
        and ev.episode_id == episode.episode_id
    ]
    assert len(fenced) == 1
    ev = fenced[0]
    assert ev.lease_id == lease.lease_id
    assert ev.owner == lease.owner
    assert ev.fencing_token == lease.fencing_token


def test_non_fenced_event_has_null_triple(store: ModelOsStore) -> None:
    store.append_event(
        EventEnvelope(
            event_id="note.1",
            kind=EventKind.NOTE,
            occurred_at="2026-07-21T00:00:00Z",
            source="test",
            provenance=Provenance.MACHINE_OBSERVATION,
            policy_version="v0",
            payload={"msg": "hi"},
        )
    )
    [(_, ev)] = store.list_events()
    assert ev.lease_id is None
    assert ev.owner is None
    assert ev.fencing_token is None


def _note(
    event_id: str = "note.dup",
    *,
    kind: str = EventKind.NOTE,
    source: str = "test",
    work_item_id: str | None = None,
    episode_id: str | None = None,
    payload: dict | None = None,
) -> EventEnvelope:
    return EventEnvelope(
        event_id=event_id,
        kind=kind,
        occurred_at="2026-07-21T00:00:00Z",
        source=source,
        provenance=Provenance.MACHINE_OBSERVATION,
        policy_version="v0",
        payload=payload or {"msg": "same"},
        work_item_id=work_item_id,
        episode_id=episode_id,
    )


def test_same_event_id_same_identity_is_idempotent(store: ModelOsStore) -> None:
    first_seq = store.append_event(_note("note.dup"))
    second_seq = store.append_event(_note("note.dup"))
    assert first_seq == second_seq
    notes = [ev for _, ev in store.list_events() if ev.event_id == "note.dup"]
    assert len(notes) == 1


def test_same_event_id_different_kind_is_conflict(store: ModelOsStore) -> None:
    store.append_event(_note("note.dup", kind=EventKind.NOTE))
    with pytest.raises(ValueError):
        store.append_event(_note("note.dup", kind=EventKind.SELF_CHANGE_PROPOSED))


def test_same_event_id_different_entity_ref_is_conflict(
    store: ModelOsStore,
) -> None:
    store.append_event(_note("note.dup", work_item_id="wi-A"))
    with pytest.raises(ValueError):
        store.append_event(_note("note.dup", work_item_id="wi-B"))


def test_same_event_id_different_source_is_conflict(store: ModelOsStore) -> None:
    store.append_event(_note("note.dup", source="runner-A"))
    with pytest.raises(ValueError):
        store.append_event(_note("note.dup", source="runner-B"))


def test_same_event_id_different_payload_is_conflict(store: ModelOsStore) -> None:
    store.append_event(_note("note.dup", payload={"msg": "first"}))
    with pytest.raises(ValueError):
        store.append_event(_note("note.dup", payload={"msg": "second"}))


def test_v2_db_migrates_to_v3_with_lease_triple_columns(tmp_path) -> None:
    import sqlite3

    from trowel_py.model_os.store import ModelOsStore

    db_path = tmp_path / "v2.db"
    # 手工构造 v2 数据库：events 缺少 lease 三元组列，meta 固定为版本 2。
    raw = sqlite3.connect(str(db_path))
    raw.executescript(
        """
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT INTO meta VALUES ('schema_version', '2');
        CREATE TABLE events (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT NOT NULL UNIQUE,
            kind TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            source TEXT NOT NULL,
            provenance TEXT NOT NULL,
            policy_version TEXT NOT NULL,
            work_item_id TEXT,
            task_id TEXT,
            episode_id TEXT,
            native_session_id TEXT,
            cause_id TEXT,
            correlation_id TEXT,
            outcome TEXT,
            payload TEXT NOT NULL,
            payload_hash TEXT
        );
        INSERT INTO events (event_id, kind, occurred_at, source, provenance,
            policy_version, payload, payload_hash)
            VALUES ('legacy.1', 'note', '2026-07-20T00:00:00Z', 'old',
                    'machine_observation', 'v0', '{"k":1}', 'sha256:x');
        """
    )
    raw.close()

    store = ModelOsStore(db_path)
    store.open()
    cols = {
        r["name"] for r in store._conn.execute("PRAGMA table_info(events)").fetchall()
    }
    for required in ("lease_id", "owner", "fencing_token"):
        assert required in cols, f"migration did not add events.{required}"
    # 当前 schema 必须同时包含后续 lease idempotency 资源域迁移。
    assert store._schema_version() >= 3

    # 旧行早于 fencing 契约，读取时 lease 三元组均为空。
    [(_, ev)] = store.list_events()
    assert ev.event_id == "legacy.1"
    assert ev.lease_id is None
    assert ev.fencing_token is None

    # 新的 fencing 写入会完整持久化 lease 三元组。
    store.append_event(
        EventEnvelope(
            event_id="note.after-migrate",
            kind=EventKind.NOTE,
            occurred_at="2026-07-21T00:00:00Z",
            source="test",
            provenance=Provenance.MACHINE_OBSERVATION,
            policy_version="v0",
            payload={"msg": "post-migrate"},
        )
    )
    store.close()
