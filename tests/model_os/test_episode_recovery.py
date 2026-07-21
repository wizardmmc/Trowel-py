"""build_recovery_partial pure-function tests (slice-087 pass criteria 7, 11).

``ModelOsStore.build_recovery_partial`` is a ``@staticmethod`` that rebuilds an
Episode snapshot from a previous snapshot + a journal high-watermark, WITHOUT
calling the model or touching the store. Per spec §设计约束:

    recovery 纯函数：build_recovery_partial 不查库不调模型；只采纳 machine
    result/evidence；journal_through_seq 截断。缺失槽位填 unknown。

These tests assert the SPEC behaviour. Any failure is a real violation, not a
helper artefact — the function takes value objects and returns one, so there
is no store state to race against.
"""

from __future__ import annotations

import inspect

from trowel_py.model_os.store import ModelOsStore
from trowel_py.model_os.types import (
    ArtifactRef,
    EpisodeSnapshot,
    EventEnvelope,
    EventKind,
    Provenance,
    SideEffectRecord,
    SnapshotRef,
    SnapshotSource,
)

from tests.model_os._episode_helpers import make_cooperative_snapshot


# --------------------------------------------------------------- purity ---


def test_build_recovery_partial_is_staticmethod() -> None:
    """The spec calls build_recovery_partial a pure function with no I/O.
    Being a staticmethod is the structural signal that it does not read
    ``self`` / the store connection — it cannot touch SQLite even if tempted."""

    attr = ModelOsStore.__dict__.get("build_recovery_partial")
    assert isinstance(attr, staticmethod), (
        "build_recovery_partial must be a @staticmethod so it has no store handle"
    )


def test_build_recovery_partial_signature_has_no_store_or_model_args() -> None:
    """No parameter may name a store, connection, session, client, or model —
    the function must be callable with only value-object inputs."""

    sig = inspect.signature(ModelOsStore.build_recovery_partial)
    forbidden_substrings = ("store", "conn", "db", "session", "client", "model", "api")
    for name in sig.parameters:
        low = name.lower()
        assert not any(bad in low for bad in forbidden_substrings), (
            f"pure function parameter {name!r} smells of I/O; recovery must not "
            f"touch a store or call a model"
        )


def test_build_recovery_partial_does_not_need_an_open_store(tmp_path) -> None:
    """Calling the function must succeed with NO store opened at all — concrete
    proof it does no I/O. (We import ModelOsStore but never call .open().)"""

    snapshot = ModelOsStore.build_recovery_partial(
        work_item_goal="g",
        task_constraints_ref=None,
        prev=None,
        journal_through_seq=42,
    )
    assert snapshot.source == SnapshotSource.RECOVERY_PARTIAL
    assert snapshot.journal_through_seq == 42


# ----------------------------------------------------------- prev = None ---


def test_recovery_with_no_base_fills_unknowns() -> None:
    """With no previous snapshot, every slot the model would have filled is
    marked unknown — recovery never invents state (spec: 缺失槽位填 unknown)."""

    snapshot = ModelOsStore.build_recovery_partial(
        work_item_goal="g",
        task_constraints_ref=None,
        prev=None,
        journal_through_seq=10,
    )
    assert snapshot.current_judgment == "unknown"
    assert snapshot.completed_with_evidence == ()
    assert snapshot.side_effects == ()
    assert snapshot.next_steps == ()
    assert snapshot.waiting_condition is None
    assert snapshot.native_transcript_ref is None
    # an explicit marker so a reader cannot mistake this for a confirmed snapshot
    assert any("unverified" in u or "no base" in u for u in snapshot.unknowns), (
        "a no-base recovery must flag itself unverified in unknowns"
    )
    assert snapshot.source == SnapshotSource.RECOVERY_PARTIAL
    assert snapshot.journal_through_seq == 10


# ---------------------------------------------- prev provided: what survives ---


def test_recovery_drops_current_judgment_and_next_steps() -> None:
    """Judgment and next_steps are model-opinion slots; after a crash they are
    NOT confirmed, so recovery must not carry them forward as if the model had
    just said them. current_judgment resets to 'unknown'; next_steps clears."""

    prev = make_cooperative_snapshot(
        current_judgment="我有八成把握",
        next_steps=("继续往下查", "写一段总结"),
    )
    snapshot = ModelOsStore.build_recovery_partial(
        work_item_goal=prev.work_item_goal,
        task_constraints_ref=None,
        prev=prev,
        journal_through_seq=99,
    )
    assert snapshot.current_judgment == "unknown"
    assert snapshot.next_steps == ()


def test_recovery_unknown_side_effects_survive_as_unknown() -> None:
    """codex N6 (round 2): a side effect whose outcome is unknown_requires_reconcile
    MUST survive into the recovery — as unknown, carrying its action_ref /
    idempotency_key so the resuming runner knows which action to check against
    reality before replay. It must NEVER appear as done and NEVER enter
    completed_with_evidence (spec pass 7: 恢复后不自动重放).

    The previous code dropped unknown side effects entirely, hiding the action
    from the recovery so the runner might believe it never happened."""

    done = SideEffectRecord(
        action_ref="action/confirmed-write",
        idempotency_key="done-1",
        outcome="done",
        evidence_ref="evidence/write.txt",
    )
    unconfirmed = SideEffectRecord(
        action_ref="action/maybe-sent-email",
        idempotency_key="unk-1",
        outcome="unknown_requires_reconcile",
    )
    prev = make_cooperative_snapshot(side_effects=(done, unconfirmed))
    snapshot = ModelOsStore.build_recovery_partial(
        work_item_goal=prev.work_item_goal,
        task_constraints_ref=None,
        prev=prev,
        journal_through_seq=5,
    )
    by_ref = {se.action_ref: se for se in snapshot.side_effects}
    # done survives as done
    assert by_ref["action/confirmed-write"].outcome == "done"
    # unknown survives AS unknown (not dropped, not promoted to done)
    assert "action/maybe-sent-email" in by_ref, (
        "unknown side effect must be preserved so the runner can check reality"
    )
    assert by_ref["action/maybe-sent-email"].outcome == "unknown_requires_reconcile"
    # only the done one enters completed_with_evidence
    assert all(
        pair[0] != "action/maybe-sent-email"
        for pair in snapshot.completed_with_evidence
    )


def test_recovery_carries_completed_with_evidence_forward() -> None:
    """completed_with_evidence entries already carry an evidence ref by the
    dataclass contract — they are confirmed, so recovery keeps them. The
    recovery must not rewrite or drop them."""

    pair1 = ("action/read-config", "evidence/config.txt")
    pair2 = ("action/parse-log", "evidence/parsed.txt")
    prev = make_cooperative_snapshot(completed_with_evidence=(pair1, pair2))
    snapshot = ModelOsStore.build_recovery_partial(
        work_item_goal=prev.work_item_goal,
        task_constraints_ref=None,
        prev=prev,
        journal_through_seq=7,
    )
    assert pair1 in snapshot.completed_with_evidence
    assert pair2 in snapshot.completed_with_evidence


def test_recovery_inherits_artifacts_and_transcript_ref() -> None:
    """Artifacts and the transcript ref are references, not model opinions —
    they survive into recovery so a reader can still open the artifact /
    transcript to verify."""

    prev = make_cooperative_snapshot(
        artifacts=(ArtifactRef(kind="commit", ref="abc123"),),
        native_transcript_ref="transcript://ep-1",
    )
    snapshot = ModelOsStore.build_recovery_partial(
        work_item_goal=prev.work_item_goal,
        task_constraints_ref=None,
        prev=prev,
        journal_through_seq=3,
    )
    assert snapshot.artifacts == prev.artifacts
    assert snapshot.native_transcript_ref == "transcript://ep-1"


def test_recovery_appends_unverified_marker_to_unknowns() -> None:
    """Even with a base snapshot, the journal beyond it is unverified — the
    recovery must say so explicitly so it is never confused with a cooperative
    snapshot."""

    prev = make_cooperative_snapshot(unknowns=("边界外的状态不确定",))
    snapshot = ModelOsStore.build_recovery_partial(
        work_item_goal=prev.work_item_goal,
        task_constraints_ref=None,
        prev=prev,
        journal_through_seq=12,
    )
    # original unknowns preserved
    assert "边界外的状态不确定" in snapshot.unknowns
    # plus an explicit recovery marker
    assert any(
        "unverified" in u or "recovery" in u.lower() for u in snapshot.unknowns
    ), "recovery must append an explicit unverified marker"


# ---------------------------------------------- journal_through_seq passthrough ---


def test_recovery_passes_journal_through_seq_through() -> None:
    """The pure function does NOT fix the high-watermark itself (that is the
    calling command's job per spec §recovery). It must echo the value it was
    given so later recovery does not re-fold already-folded events."""

    for seq in (0, 1, 42, 1000):
        snapshot = ModelOsStore.build_recovery_partial(
            work_item_goal="g",
            task_constraints_ref=None,
            prev=None,
            journal_through_seq=seq,
        )
        assert snapshot.journal_through_seq == seq


def test_recovery_source_is_recovery_partial_even_from_cooperative_base() -> None:
    """A recovery built from a cooperative base is still a recovery — source
    tracks how THIS snapshot was produced, not its base."""

    prev = make_cooperative_snapshot()  # source = COOPERATIVE
    snapshot = ModelOsStore.build_recovery_partial(
        work_item_goal=prev.work_item_goal,
        task_constraints_ref=None,
        prev=prev,
        journal_through_seq=0,
    )
    assert snapshot.source == SnapshotSource.RECOVERY_PARTIAL


def test_recovery_uses_provided_goal_and_constraints_ref() -> None:
    """work_item_goal / task_constraints_ref come from the calling command
    (which reads them from the live Task / WorkItem); recovery carries them
    through verbatim."""

    prev = make_cooperative_snapshot(work_item_goal="old goal")
    snapshot = ModelOsStore.build_recovery_partial(
        work_item_goal="the real goal from the live task",
        task_constraints_ref="task-123",
        prev=prev,
        journal_through_seq=0,
    )
    assert snapshot.work_item_goal == "the real goal from the live task"
    assert snapshot.task_constraints_ref == "task-123"


# ---------------------------------------------- base_snapshot_ref lineage ---


def test_recovery_base_snapshot_ref_is_not_a_corrupt_dummy() -> None:
    """When a base snapshot has no base of its own (a cooperative base), the
    recovery's base_snapshot_ref must NOT be a fabricated SnapshotRef with
    empty strings — that is a corrupt reference no reader can resolve.

    The clean spec-faithful options are (a) None, or (b) the prev snapshot's
    own SnapshotRef (which the calling command knows via ep.last_snapshot_ref).
    A dummy (episode_id='', version=0) is neither and is a bug."""

    prev = make_cooperative_snapshot()  # cooperative → prev.base_snapshot_ref is None
    snapshot = ModelOsStore.build_recovery_partial(
        work_item_goal=prev.work_item_goal,
        task_constraints_ref=None,
        prev=prev,
        journal_through_seq=0,
    )
    base = snapshot.base_snapshot_ref
    if base is not None:
        # if a ref is given, it must be resolvable: a non-empty episode_id and a
        # strictly positive version. Empty episode_id == corrupt dummy.
        assert base.episode_id != "", (
            "recovery base_snapshot_ref must point at a real snapshot, not a "
            "dummy with empty episode_id"
        )
        assert base.version > 0, (
            "recovery base_snapshot_ref version must be a real version (>0), "
            "not the placeholder 0"
        )


# ---------------------------------------------- M4: base points at prev_ref ---


def test_recovery_base_points_at_supplied_prev_ref() -> None:
    """M4: when the calling command supplies the prev snapshot's ref, the
    recovery's base_snapshot_ref MUST equal it — not the prev's own base, not a
    dummy. This keeps the recovery lineage accurate so later recovery /
    journal watermark reasoning holds."""

    prev = make_cooperative_snapshot()
    prev_ref = SnapshotRef(
        episode_id="ep-1",
        version=3,
        committed_event_id="evt-orig",
        payload_hash="sha256:abc",
    )
    snapshot = ModelOsStore.build_recovery_partial(
        work_item_goal=prev.work_item_goal,
        task_constraints_ref=None,
        prev=prev,
        prev_ref=prev_ref,
        journal_through_seq=42,
    )
    assert snapshot.base_snapshot_ref == prev_ref


def test_recovery_chain_does_not_collapse_to_grandparent() -> None:
    """M4 regression: a recovery built on a recovery must point at its immediate
    parent, not skip to the grandparent. (The previous code copied
    prev.base_snapshot_ref, collapsing the chain.)"""

    grandparent_ref = SnapshotRef(
        episode_id="ep-1", version=1, committed_event_id="g", payload_hash="g"
    )
    parent = EpisodeSnapshot(
        work_item_goal="g",
        task_constraints_ref=None,
        current_judgment="unknown",
        completed_with_evidence=(),
        side_effects=(),
        unknowns=("u",),
        waiting_condition=None,
        next_steps=(),
        artifacts=(),
        native_transcript_ref=None,
        source=SnapshotSource.RECOVERY_PARTIAL,
        journal_through_seq=10,
        base_snapshot_ref=grandparent_ref,
    )
    parent_ref = SnapshotRef(
        episode_id="ep-1", version=2, committed_event_id="p", payload_hash="p"
    )
    snapshot = ModelOsStore.build_recovery_partial(
        work_item_goal="g",
        task_constraints_ref=None,
        prev=parent,
        prev_ref=parent_ref,
        journal_through_seq=20,
    )
    assert snapshot.base_snapshot_ref == parent_ref, (
        "recovery base must point at the immediate parent, not the grandparent"
    )


# ---------------------------------------------- H6: fold journal done side effects ---


def _side_effect_event(
    *,
    action_ref: str,
    outcome: str,
    evidence_ref: str | None = None,
    idempotency_key: str = "se-key",
    event_id: str | None = None,
) -> EventEnvelope:
    return EventEnvelope(
        event_id=event_id or f"se.{action_ref}.{outcome}",
        kind=EventKind.EPISODE_SIDE_EFFECT_RECORDED,
        occurred_at="2026-07-21T00:00:06Z",
        source="runner-A",
        provenance=Provenance.MACHINE_OBSERVATION,
        policy_version="v0",
        payload={
            "action_ref": action_ref,
            "idempotency_key": idempotency_key,
            "outcome": outcome,
            "evidence_ref": evidence_ref,
        },
        episode_id="ep-1",
    )


def test_recovery_folds_done_side_effects_from_journal() -> None:
    """H6: a done side effect recorded AFTER the last checkpoint (in the
    journal range) MUST survive into the recovery. The previous code copied
    only prev, so a confirmed action between checkpoint and crash was lost —
    and replay might re-execute it."""

    prev = make_cooperative_snapshot()  # no side effects in prev beyond default
    done_event = _side_effect_event(
        action_ref="action/send-email",
        outcome="done",
        evidence_ref="evidence/sent-receipt.txt",
    )
    snapshot = ModelOsStore.build_recovery_partial(
        work_item_goal=prev.work_item_goal,
        task_constraints_ref=None,
        prev=prev,
        journal_through_seq=99,
        events=(done_event,),
    )
    folded = [se for se in snapshot.side_effects if se.action_ref == "action/send-email"]
    assert len(folded) == 1
    assert folded[0].outcome == "done"
    assert folded[0].evidence_ref == "evidence/sent-receipt.txt"
    # the done action also lands in completed_with_evidence
    assert ("action/send-email", "evidence/sent-receipt.txt") in snapshot.completed_with_evidence


def test_recovery_preserves_unconfirmed_side_effects_as_unknown() -> None:
    """codex N6 (round 2): a journal side_effect_recorded with outcome
    unknown_requires_reconcile must be PRESERVED into the recovery as unknown
    (carrying action_ref / idempotency_key), never promoted to done, never
    entered into completed_with_evidence. Reality must be checked before replay."""

    prev = make_cooperative_snapshot()
    unconfirmed = _side_effect_event(
        action_ref="action/maybe-charged-card",
        outcome="unknown_requires_reconcile",
    )
    snapshot = ModelOsStore.build_recovery_partial(
        work_item_goal=prev.work_item_goal,
        task_constraints_ref=None,
        prev=prev,
        journal_through_seq=5,
        events=(unconfirmed,),
    )
    by_ref = {se.action_ref: se for se in snapshot.side_effects}
    assert "action/maybe-charged-card" in by_ref, (
        "unknown side effect must be preserved so the runner can check reality"
    )
    assert by_ref["action/maybe-charged-card"].outcome == "unknown_requires_reconcile"
    assert all(
        pair[0] != "action/maybe-charged-card"
        for pair in snapshot.completed_with_evidence
    ), "unconfirmed side effects must never enter completed_with_evidence"


def test_recovery_does_not_double_count_action_already_in_prev() -> None:
    """H6: if a done side effect is already in prev (folded into a prior
    checkpoint), the journal event for it must not duplicate. The
    journal_through_seq window excludes events already in prev, but the fold
    is also idempotent on action_ref as a backstop."""

    done = SideEffectRecord(
        action_ref="action/x",
        idempotency_key="k",
        outcome="done",
        evidence_ref="e/x",
    )
    prev = make_cooperative_snapshot(side_effects=(done,))
    dup_event = _side_effect_event(
        action_ref="action/x", outcome="done", evidence_ref="e/x"
    )
    snapshot = ModelOsStore.build_recovery_partial(
        work_item_goal=prev.work_item_goal,
        task_constraints_ref=None,
        prev=prev,
        journal_through_seq=10,
        events=(dup_event,),
    )
    xs = [se for se in snapshot.side_effects if se.action_ref == "action/x"]
    assert len(xs) == 1, "an action already in prev must not be duplicated by the fold"


def test_recovery_ignores_non_side_effect_journal_events() -> None:
    """The fold only consumes ``episode.side_effect_recorded`` events. Other
    events in the slice (status changes, notes) must not perturb the recovery."""

    prev = make_cooperative_snapshot()
    note = EventEnvelope(
        event_id="note.1",
        kind=EventKind.NOTE,
        occurred_at="2026-07-21T00:00:07Z",
        source="runner",
        provenance=Provenance.MACHINE_OBSERVATION,
        policy_version="v0",
        payload={"msg": "during the work"},
        episode_id="ep-1",
    )
    snapshot = ModelOsStore.build_recovery_partial(
        work_item_goal=prev.work_item_goal,
        task_constraints_ref=None,
        prev=prev,
        journal_through_seq=8,
        events=(note,),
    )
    # only prev's default done side effect remains; nothing added
    assert all(
        se.action_ref != "note" for se in snapshot.side_effects
    )
