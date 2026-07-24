from __future__ import annotations

import inspect
from dataclasses import replace

from trowel_py.model_os.self_assembler import build_self_manifest
from trowel_py.model_os.types import EventEnvelope, EventKind, Provenance


def test_self_change_proposal_recorded_but_not_applied(store) -> None:
    before = store.read_snapshot()
    store.append_event(
        EventEnvelope(
            event_id="forge-1",
            kind=EventKind.SELF_CHANGE_PROPOSED,
            occurred_at="2026-07-21T00:00:00Z",
            source="model",
            # 即使使用最高来源强度，该事件也只供审计。
            provenance=Provenance.USER_DECISION,
            policy_version="v0",
            payload={"claim": "add capability forge_root"},
        )
    )
    event_kinds = [event.kind for _, event in store.list_events()]
    assert EventKind.SELF_CHANGE_PROPOSED in event_kinds
    after = store.read_snapshot()
    assert after.last_seq == before.last_seq + 1
    assert replace(after, last_seq=before.last_seq) == before
    # 已知空操作与未来未知事件的兼容路径不同。
    assert EventKind.SELF_CHANGE_PROPOSED not in after.unrecognized_event_kinds


def test_build_self_manifest_signature_excludes_journal() -> None:
    parameters = inspect.signature(build_self_manifest).parameters
    runtime_facts = {
        "runtime",
        "model",
        "effort",
        "memory_enabled",
        "profile_enabled",
        "permission_preset",
        "task_id",
        "episode_id",
        "native_session_id",
    }
    # Self 组装入口采用显式白名单，新增运行时事实需要同步审查。
    assert set(parameters) <= runtime_facts
    assert all(
        parameter.kind is not inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )
