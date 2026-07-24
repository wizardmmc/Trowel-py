from __future__ import annotations

import hashlib
from dataclasses import replace
from typing import Any

import pytest

from tests.model_os._episode_helpers import (
    FakeClock,
    activate_episode,
    make_cooperative_snapshot,
    make_running_system_episode,
)
from trowel_py.model_os import store as store_module
from trowel_py.model_os.store import ModelOsStore, StaleWriterRejected


def test_checkpoint_keeps_snapshot_write_order(
    store: ModelOsStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FakeClock()
    clock.install(monkeypatch)
    episode, lease, _ = make_running_system_episode(store)
    activate_episode(store, episode.episode_id, lease)
    store.request_yield(
        episode.episode_id,
        expected_lease_id=lease.lease_id,
        expected_owner=lease.owner,
        expected_token=lease.fencing_token,
        reason="checkpoint",
    )

    calls: list[Any] = []
    original_encode = store_module._snapshot_to_payload
    original_payload_json = store_module._payload_json
    original_validate = store_module._validate_episode_snapshot
    original_append = store._append_fenced_event_in_tx

    def encode(snapshot):
        calls.append("encode")
        return original_encode(snapshot)

    def payload_json(payload):
        payload_text, payload_hash = original_payload_json(payload)
        digest = hashlib.sha256(payload_text.encode("utf-8")).hexdigest()[:12]
        calls.append(
            (
                "redact_hash",
                "Bearer example-session-token" not in payload_text,
                "<redacted:" in payload_text,
                payload_hash == f"sha256:{digest}",
                store._conn.in_transaction,
            )
        )
        return payload_text, payload_hash

    def validate(snapshot, payload_text):
        calls.append("validate")
        return original_validate(snapshot, payload_text)

    def append_event(event):
        row_count = store._conn.execute(
            "SELECT COUNT(*) FROM episode_snapshots WHERE episode_id=?",
            (episode.episode_id,),
        ).fetchone()[0]
        calls.append(("append_event", row_count))
        return original_append(event)

    monkeypatch.setattr(store_module, "_snapshot_to_payload", encode)
    monkeypatch.setattr(store_module, "_payload_json", payload_json)
    monkeypatch.setattr(
        store_module,
        "_validate_episode_snapshot",
        validate,
    )
    monkeypatch.setattr(store, "_append_fenced_event_in_tx", append_event)

    store.commit_checkpoint(
        episode.episode_id,
        expected_lease_id=lease.lease_id,
        expected_owner=lease.owner,
        expected_token=lease.fencing_token,
        snapshot=replace(
            make_cooperative_snapshot(),
            current_judgment="Bearer example-session-token",
        ),
        checkpoint_key="checkpoint-order",
    )

    assert calls[:4] == [
        "encode",
        ("redact_hash", True, True, True, True),
        "validate",
        ("append_event", 1),
    ]


def test_checkpoint_rolls_back_snapshot_when_fencing_fails(
    store: ModelOsStore, monkeypatch
) -> None:
    clock = FakeClock()
    clock.install(monkeypatch)
    episode, lease, _ = make_running_system_episode(store, ttl_seconds=60)
    activate_episode(store, episode.episode_id, lease)
    store.request_yield(
        episode.episode_id,
        expected_lease_id=lease.lease_id,
        expected_owner=lease.owner,
        expected_token=lease.fencing_token,
        reason="done",
    )
    clock.advance(61)

    with pytest.raises(StaleWriterRejected):
        store.commit_checkpoint(
            episode.episode_id,
            expected_lease_id=lease.lease_id,
            expected_owner=lease.owner,
            expected_token=lease.fencing_token,
            snapshot=make_cooperative_snapshot(),
            checkpoint_key="ck-atomic",
        )
    connection = store._conn
    assert connection is not None
    row = connection.execute(
        "SELECT COUNT(*) AS n FROM episode_snapshots "
        "WHERE episode_id=? AND checkpoint_key=?",
        (episode.episode_id, "ck-atomic"),
    ).fetchone()
    # fenced checkpoint event 写入失败时，先插入的快照行必须随事务回滚。
    assert int(row["n"]) == 0
