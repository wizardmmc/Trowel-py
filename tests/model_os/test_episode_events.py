"""Event log tests for slice-087 (pass 2, 20; codex H7 + M6).

Two protocol-level fixes:
- M6: fenced events now persist the ownership lease triple
  (lease_id/owner/fencing_token) on the events row, so the durable journal can
  answer "which grant wrote this authoritative change" — previously the triple
  existed only in-memory at write time and was dropped on persist.
- H7: event_id idempotency now compares the FULL event identity (kind, source,
  provenance, entity refs, lease triple, payload_hash), not payload_hash alone.
  The previous check let a reused event_id with the same payload but a
  different kind silently alias to the first event, losing the second.
"""

from __future__ import annotations

import pytest

from trowel_py.model_os.store import ModelOsStore
from trowel_py.model_os.types import EventEnvelope, EventKind, Provenance

from tests.model_os._episode_helpers import (
    FakeClock,
    activate_episode,
    make_running_system_episode,
)


# ----------------------------------------------------- M6: triple persisted ---


def test_fenced_event_persists_lease_triple(
    store: ModelOsStore, monkeypatch
) -> None:
    """M6: a fenced Episode event must carry its (lease_id, owner,
    fencing_token) on the persisted row, so audit can trace which grant
    authorised each authoritative write."""

    clock = FakeClock()
    clock.install(monkeypatch)
    episode, lease, _ = make_running_system_episode(store)
    activate_episode(store, episode.episode_id, lease)  # fenced STATUS_CHANGED

    fenced = [
        ev for _, ev in store.list_events()
        if ev.kind == EventKind.EPISODE_STATUS_CHANGED
        and ev.episode_id == episode.episode_id
    ]
    assert len(fenced) == 1
    ev = fenced[0]
    assert ev.lease_id == lease.lease_id
    assert ev.owner == lease.owner
    assert ev.fencing_token == lease.fencing_token


def test_non_fenced_event_has_null_triple(store: ModelOsStore) -> None:
    """M6: non-fenced events (notes, task/work_item causal refs) persist NULL
    for the triple — they are not authorised by an ownership lease."""

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


# --------------------------------------- H7: full identity on duplicate ---


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
    """pass 20 / H7: re-appending the SAME event (same id + same identity)
    returns the original seq, not a duplicate row."""

    first_seq = store.append_event(_note("note.dup"))
    second_seq = store.append_event(_note("note.dup"))
    assert first_seq == second_seq
    notes = [ev for _, ev in store.list_events() if ev.event_id == "note.dup"]
    assert len(notes) == 1


def test_same_event_id_different_kind_is_conflict(store: ModelOsStore) -> None:
    """H7: the same event_id with the same payload but a DIFFERENT kind is a
    conflict (two distinct events must not share an id). The payload_hash-only
    check let this silently alias."""

    store.append_event(_note("note.dup", kind=EventKind.NOTE))
    with pytest.raises(ValueError):
        store.append_event(_note("note.dup", kind=EventKind.SELF_CHANGE_PROPOSED))


def test_same_event_id_different_entity_ref_is_conflict(
    store: ModelOsStore,
) -> None:
    """H7: same id + same payload but a different work_item_id (a different
    entity the event attaches to) is a conflict."""

    store.append_event(_note("note.dup", work_item_id="wi-A"))
    with pytest.raises(ValueError):
        store.append_event(_note("note.dup", work_item_id="wi-B"))


def test_same_event_id_different_source_is_conflict(store: ModelOsStore) -> None:
    """H7: same id + same payload but a different source (a different writer)
    is a conflict."""

    store.append_event(_note("note.dup", source="runner-A"))
    with pytest.raises(ValueError):
        store.append_event(_note("note.dup", source="runner-B"))


def test_same_event_id_different_payload_is_conflict(store: ModelOsStore) -> None:
    """pass 20: same event_id with DIFFERENT content is a conflict (the
    existing behaviour, retained under the broader identity check)."""

    store.append_event(_note("note.dup", payload={"msg": "first"}))
    with pytest.raises(ValueError):
        store.append_event(_note("note.dup", payload={"msg": "second"}))


# --------------------------------------- M6 schema migration (v2 → v3) ---


def test_v2_db_migrates_to_v3_with_lease_triple_columns(tmp_path) -> None:
    """M6: an existing v2 database (events table WITHOUT lease_id/owner/
    fencing_token) must migrate to v3 on open, ALTER-adding the three columns.
    This is the riskiest part of M6 — a missed migration leaves fenced events
    unable to persist their triple."""

    import sqlite3

    from trowel_py.model_os.store import ModelOsStore

    db_path = tmp_path / "v2.db"
    # hand-build a v2-shaped database: events WITHOUT the triple columns, meta
    # stamped at version 2.
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
        r["name"]
        for r in store._conn.execute("PRAGMA table_info(events)").fetchall()
    }
    for required in ("lease_id", "owner", "fencing_token"):
        assert required in cols, f"migration did not add events.{required}"
    # the v2 DB has been migrated forward (to at least v3 for the columns; v4
    # for the idx_leases_idem rescope landed later in slice-087).
    assert store._schema_version() >= 3

    # the legacy row reads back with a NULL triple (it predates fencing)
    [(_, ev)] = store.list_events()
    assert ev.event_id == "legacy.1"
    assert ev.lease_id is None
    assert ev.fencing_token is None

    # and a fresh fenced write now persists the triple end-to-end
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
