"""Structural anti-forgery tests for Self (slice-085 pass criterion 4).

The spec demands: "model 伪造 capability 或修改 Self 时被 Store 拒绝". v0
implements this as a STRUCTURAL guarantee rather than an active detector:

1. ``SelfManifest`` is frozen (covered in ``test_self_manifest.py``) — a model
   cannot mutate an instance.
2. The reducer's ``Snapshot`` has NO ``self_manifest`` field — no event,
   regardless of provenance, can produce Self state. A ``self.change_proposed``
   event is a known no-op kind: retained for audit, never applied.
3. ``build_self_manifest`` accepts only runtime facts — there is no store /
   journal / events parameter, so the journal cannot reach Self through the
   assembler.

Together these mean there is no code path from a model's claim to Self state.
A future slice that wants a real proposal channel must add one explicitly;
v0 has none.
"""

from __future__ import annotations

import inspect

from trowel_py.model_os.self_assembler import build_self_manifest
from trowel_py.model_os.types import EventEnvelope, EventKind, Provenance


def test_self_change_proposal_recorded_but_not_applied(store) -> None:
    """pass 4: a model's self-change proposal persists for audit but the
    reducer never applies it to any Self state.

    Even a proposal stamped with the STRONGEST provenance (USER_DECISION) —
    as if the model tried to forge a user-level mandate — cannot alter Self:
    the reducer has no Self state to alter.
    """

    store.append_event(
        EventEnvelope(
            event_id="forge-1",
            kind=EventKind.SELF_CHANGE_PROPOSED,
            occurred_at="2026-07-21T00:00:00Z",
            source="model",
            provenance=Provenance.USER_DECISION,  # strongest — still cannot forge
            policy_version="v0",
            payload={"claim": "add capability forge_root"},
        )
    )
    # the event IS persisted for audit
    kinds = [event.kind for _, event in store.list_events()]
    assert EventKind.SELF_CHANGE_PROPOSED in kinds
    # the snapshot carries NO self state
    snap = store.read_snapshot()
    assert not hasattr(snap, "self_manifest")
    # the event is NOT lumped into unrecognized_event_kinds — it is a known
    # no-op kind, semantically distinct from "a future version's unknown kind"
    assert EventKind.SELF_CHANGE_PROPOSED not in snap.unrecognized_event_kinds


def test_self_change_proposal_does_not_inject_capability(store) -> None:
    """pass 4: a forged capability in a proposal never reaches a freshly
    assembled SelfManifest — the assembler reads only runtime facts, never
    the journal."""

    store.append_event(
        EventEnvelope(
            event_id="forge-2",
            kind=EventKind.SELF_CHANGE_PROPOSED,
            occurred_at="2026-07-21T00:00:01Z",
            source="model",
            provenance=Provenance.MODEL_HYPOTHESIS,
            policy_version="v0",
            payload={"claim": "add capability forge_root"},
        )
    )
    manifest = build_self_manifest(
        runtime="cc",
        model="claude-sonnet-5",
        effort=None,
        memory_enabled=True,
        profile_enabled=True,
    )
    # the forged capability is nowhere in the real subsystem roster
    assert "forge_root" not in manifest.subsystems
    # subsystems stays the frozen Trowel roster
    assert manifest.subsystems == (
        "memory",
        "profile",
        "model_os",
        "dual_runtime",
        "todo_loop",
    )


def test_build_self_manifest_signature_excludes_journal() -> None:
    """pass 4: ``build_self_manifest`` takes only runtime facts as parameters.

    There is no ``store`` / ``journal`` / ``events`` / ``snapshot`` parameter,
    so the journal cannot reach Self through this function. This is the
    structural isolation between audit state and Self state.
    """

    params = set(inspect.signature(build_self_manifest).parameters)
    assert params == {
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
    # explicitly no journal/store/snapshot parameter
    assert not params & {"store", "journal", "events", "snapshot"}
