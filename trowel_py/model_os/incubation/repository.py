"""IncubationPlan 与用户控制命令的事务仓储。"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from uuid import uuid4

from trowel_py.model_os.types import (
    EventEnvelope,
    EventKind,
    MemoryEligibility,
    Provenance,
    SessionPurpose,
    TaskStatus,
    WaitingCondition,
    WorkItemKind,
    WorkItemStatus,
)
from trowel_py.model_os.candidates import CandidateStatus
from trowel_py.model_os.work_broker import BudgetDimensions
from trowel_py.model_os.waking import WakeCatchupPolicy, WakeConditionKind

from .codec import (
    decode_snapshot_ref,
    decode_wake_condition,
    digest,
    encode_json,
    encode_snapshot_ref,
    utc_iso,
    wake_condition_payload,
)
from .models import (
    MAX_SCHEDULED_CYCLES,
    POLICY_VERSION,
    REFRAME_POLICY,
    CreateIncubationPlanCommand,
    IncubationError,
    IncubationCandidate,
    IncubationCandidateDraft,
    IncubationGateReport,
    IncubationPlan,
    IncubationPlanStatus,
    IncubationResult,
    IncubationUsage,
    IncubationWakeCondition,
    parse_instant,
)


class IncubationRepository:
    def __init__(self, store) -> None:
        self._store = store

    @property
    def _conn(self):
        conn = self._store._conn
        if conn is None:
            raise RuntimeError("ModelOsStore is not open")
        return conn

    @staticmethod
    def _fingerprint(command: CreateIncubationPlanCommand) -> str:
        return digest(
            encode_json(
                {
                    "task_id": command.task_id,
                    "prepared_snapshot": asdict(command.prepared_snapshot_ref),
                    "unresolved_question": command.unresolved_question,
                    "wake_condition": wake_condition_payload(command.wake_condition),
                    "deadline": command.deadline,
                    "budget": asdict(command.budget),
                    "runtime": command.runtime,
                }
            )
        )

    def create_plan(self, command: CreateIncubationPlanCommand) -> IncubationPlan:
        stamp = utc_iso(command.occurred_at)
        fingerprint = self._fingerprint(command)
        with self._store._tx():
            self._claim_command_id(
                command.command_id, "create", fingerprint, stamp
            )
            existing = self._conn.execute(
                "SELECT plan_id, command_fingerprint FROM incubation_plans "
                "WHERE command_id=?",
                (command.command_id,),
            ).fetchone()
            if existing is not None:
                if existing["command_fingerprint"] != fingerprint:
                    raise IncubationError("idempotency_conflict")
                return self.get_plan(existing["plan_id"])

            snapshot = self._store.replay()
            task = next(
                (item for item in snapshot.tasks if item.task_id == command.task_id),
                None,
            )
            if task is None:
                raise IncubationError("task_missing")
            if task.status is not TaskStatus.RUNNING:
                raise IncubationError("task_not_eligible")
            previous = snapshot.episode_by_id(command.prepared_snapshot_ref.episode_id)
            if previous is None:
                raise IncubationError("snapshot_missing")
            if previous.task_id != task.task_id:
                raise IncubationError("snapshot_task_mismatch")
            if previous.last_snapshot_ref != command.prepared_snapshot_ref:
                raise IncubationError("snapshot_stale")
            try:
                self._store.read_episode_snapshot(command.prepared_snapshot_ref)
            except (KeyError, ValueError) as exc:
                raise IncubationError("snapshot_missing") from exc
            self._validate_deadline(command)

            plan_id = uuid4().hex
            work_item = self._store.create_work_item(
                kind=WorkItemKind.INCUBATION,
                owner_ref=f"task:{task.task_id}",
                task_id=task.task_id,
                session_purpose=SessionPurpose.INCUBATION,
                memory_eligibility=MemoryEligibility.INELIGIBLE,
            )
            self._conn.execute(
                "INSERT INTO incubation_plans VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    plan_id,
                    command.command_id,
                    fingerprint,
                    task.task_id,
                    work_item.work_item_id,
                    encode_snapshot_ref(command.prepared_snapshot_ref),
                    command.unresolved_question,
                    encode_json(wake_condition_payload(command.wake_condition)),
                    command.deadline,
                    encode_json(asdict(command.budget)),
                    command.runtime,
                    0,
                    MAX_SCHEDULED_CYCLES,
                    IncubationPlanStatus.PENDING_WAKE.value,
                    None,
                    None,
                    0,
                    1,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    POLICY_VERSION,
                    REFRAME_POLICY,
                    stamp,
                    stamp,
                ),
            )
            self._store._set_waiting_in_tx(
                task.task_id,
                WaitingCondition(
                    kind=TaskStatus.INCUBATING.value,
                    cause=command.unresolved_question,
                    condition_kind="incubation",
                    target_ref=f"incubation:{plan_id}",
                    match_params={"plan_id": plan_id},
                    deadline=command.deadline,
                    open_question=command.unresolved_question,
                    preparation_snapshot_ref=encode_snapshot_ref(
                        command.prepared_snapshot_ref
                    ),
                    earliest_review_at=command.wake_condition.due_at,
                    condition_id=f"incubation:{plan_id}",
                    registered_at=stamp,
                    catchup_policy=(
                        WakeCatchupPolicy.MERGE_ONCE.value
                        if command.wake_condition.kind is WakeConditionKind.TIME
                        else WakeCatchupPolicy.NO_CATCHUP.value
                    ),
                ),
                snapshot,
            )
            self._record_create_event(command, plan_id, work_item.work_item_id, stamp)
            return self.get_plan(plan_id)

    def cancel_plan(
        self, plan_id: str, *, command_id: str, occurred_at: datetime
    ) -> IncubationPlan:
        stamp = utc_iso(occurred_at)
        fingerprint = digest(encode_json(["cancel", plan_id]))
        with self._store._tx():
            self._claim_command_id(command_id, "cancel", fingerprint, stamp)
            previous = self._conn.execute(
                "SELECT fingerprint, result_json FROM incubation_control_commands "
                "WHERE command_id=?",
                (command_id,),
            ).fetchone()
            if previous is not None:
                if previous["fingerprint"] != fingerprint:
                    raise IncubationError("idempotency_conflict")
                return self.get_plan(json.loads(previous["result_json"])["plan_id"])
            row = self._plan_row(plan_id)
            if IncubationPlanStatus(row["status"]) not in {
                IncubationPlanStatus.PENDING_WAKE,
                IncubationPlanStatus.READY,
                IncubationPlanStatus.AWAITING_REVIEW,
            }:
                raise IncubationError("plan_not_cancellable")
            self._conn.execute(
                "UPDATE incubation_plans SET status='cancelled', "
                "stop_reason='user_cancelled', updated_at=? WHERE plan_id=?",
                (stamp, plan_id),
            )
            self._conn.execute(
                "UPDATE incubation_candidates SET status='expired', outcome_at=?, "
                "outcome_reason='user_cancelled', expires_at=? WHERE plan_id=? "
                "AND status IN ('new','shown')",
                (stamp, stamp, plan_id),
            )
            self._cancel_work_item_and_restore_task(row, stamp)
            result = encode_json({"plan_id": plan_id})
            self._conn.execute(
                "INSERT INTO incubation_control_commands VALUES (?,?,?,?,?,?)",
                (command_id, fingerprint, plan_id, "cancel", result, stamp),
            )
            self._record_control_event(
                command_id,
                row,
                command_kind="incubation.cancel_plan",
                result_code="user_cancelled",
                stamp=stamp,
            )
            return self.get_plan(plan_id)

    def begin_run(self, plan_id: str, *, occurred_at: datetime) -> IncubationPlan:
        stamp = utc_iso(occurred_at)
        failure_code: str | None = None
        result: IncubationPlan | None = None
        with self._store._tx():
            row = self._plan_row(plan_id)
            if row["status"] == IncubationPlanStatus.RUNNING.value:
                return self._decode_plan(row)
            if row["status"] != IncubationPlanStatus.READY.value:
                raise IncubationError("plan_not_ready")
            if row["deadline"] is not None and parse_instant(
                row["deadline"], "deadline"
            ) <= occurred_at.astimezone(timezone.utc):
                self._fail_before_run(row, "deadline_expired", stamp)
                failure_code = "deadline_expired"
            else:
                snapshot = self._store.replay()
                task = next(
                    (
                        item
                        for item in snapshot.tasks
                        if item.task_id == row["task_id"]
                    ),
                    None,
                )
                if (
                    task is None
                    or task.status is not TaskStatus.INCUBATING
                    or task.waiting_condition is None
                    or task.waiting_condition.condition_id
                    != f"incubation:{row['plan_id']}"
                ):
                    self._fail_before_run(
                        row, "task_changed", stamp, restore_task=False
                    )
                    failure_code = "task_changed"
                else:
                    ref = decode_snapshot_ref(row["prepared_snapshot_json"])
                    episode = snapshot.episode_by_id(ref.episode_id)
                    try:
                        snapshot_payload = self._store.read_episode_snapshot(ref)
                    except (KeyError, ValueError):
                        snapshot_payload = None
                    if (
                        episode is None
                        or episode.last_snapshot_ref != ref
                        or snapshot_payload is None
                    ):
                        self._fail_before_run(row, "snapshot_stale", stamp)
                        failure_code = "snapshot_stale"
                    else:
                        self._conn.execute(
                            "UPDATE incubation_plans SET status='running', "
                            "broker_settled=0, updated_at=? "
                            "WHERE plan_id=? AND status='ready'",
                            (stamp, plan_id),
                        )
                        self._record_cycle_intent(row, stamp)
                        result = self.get_plan(plan_id)
        if failure_code is not None:
            raise IncubationError(failure_code)
        assert result is not None
        return result

    def attach_episode(self, plan_id: str, episode_id: str) -> None:
        with self._store._tx():
            self._conn.execute(
                "UPDATE incubation_plans SET episode_id=COALESCE(episode_id, ?) "
                "WHERE plan_id=?",
                (episode_id, plan_id),
            )

    def record_terminal_output(
        self,
        plan_id: str,
        *,
        episode_id: str,
        effective_model: str,
        validated_output_json: str,
        usage: IncubationUsage,
        occurred_at: datetime,
    ) -> None:
        stamp = utc_iso(occurred_at)
        with self._store._tx():
            row = self._plan_row(plan_id)
            expected = (
                episode_id,
                effective_model,
                validated_output_json,
                usage.input_tokens,
                usage.output_tokens,
                usage.wall_seconds,
                usage.cost,
            )
            actual = (
                row["episode_id"],
                row["effective_model"],
                row["validated_output_json"],
                row["input_tokens"],
                row["output_tokens"],
                row["wall_seconds"],
                row["cost"],
            )
            if row["validated_output_json"] is not None:
                if actual != expected:
                    raise IncubationError("result_unknown")
                return
            if row["status"] != IncubationPlanStatus.RUNNING.value:
                raise IncubationError("result_unknown")
            self._conn.execute(
                "UPDATE incubation_plans SET episode_id=?, model_called=1, "
                "effective_model=?, validated_output_json=?, input_tokens=?, "
                "output_tokens=?, wall_seconds=?, cost=?, updated_at=? WHERE plan_id=?",
                (
                    episode_id,
                    effective_model,
                    validated_output_json,
                    usage.input_tokens,
                    usage.output_tokens,
                    usage.wall_seconds,
                    usage.cost,
                    stamp,
                    plan_id,
                ),
            )

    def commit_generation(
        self,
        plan_id: str,
        draft: IncubationCandidateDraft,
        *,
        effective_model: str,
        usage: IncubationUsage,
        occurred_at: datetime,
    ) -> IncubationResult:
        stamp = utc_iso(occurred_at)
        with self._store._tx():
            row = self._plan_row(plan_id)
            existing = self._candidate_for_plan(plan_id)
            if row["status"] == IncubationPlanStatus.AWAITING_REVIEW.value:
                return IncubationResult(self._decode_plan(row), existing)
            if row["status"] == IncubationPlanStatus.STOPPED.value and row[
                "stop_reason"
            ] == "no_increment":
                return IncubationResult(self._decode_plan(row), None)
            if row["status"] != IncubationPlanStatus.RUNNING.value:
                raise IncubationError("result_unknown")
            if not draft.new_points:
                if not self._task_owned_by_plan(row):
                    self._fail_before_run(
                        row, "task_changed", stamp, restore_task=False
                    )
                    return IncubationResult(self.get_plan(plan_id), None)
                self._conn.execute(
                    "UPDATE incubation_plans SET status='stopped', "
                    "stop_reason='no_increment', effective_model=?, input_tokens=?, "
                    "output_tokens=?, wall_seconds=?, cost=?, updated_at=? WHERE plan_id=?",
                    (
                        effective_model,
                        usage.input_tokens,
                        usage.output_tokens,
                        usage.wall_seconds,
                        usage.cost,
                        stamp,
                        plan_id,
                    ),
                )
                self._restore_task_ready(row, stamp)
                self._record_cycle_result(row, "no_increment", (), stamp)
                return IncubationResult(self.get_plan(plan_id), None)
            if not self._task_owned_by_plan(row):
                self._fail_before_run(row, "task_changed", stamp, restore_task=False)
                return IncubationResult(self.get_plan(plan_id), None)
            candidate_id = uuid4().hex
            self._conn.execute(
                "INSERT INTO incubation_candidates VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    candidate_id,
                    plan_id,
                    1,
                    draft.proposal,
                    encode_json(draft.source_refs),
                    encode_json(draft.new_points),
                    draft.verification,
                    draft.uncertainty,
                    row["runtime"],
                    effective_model,
                    "deep",
                    POLICY_VERSION,
                    CandidateStatus.NEW.value,
                    stamp,
                    None,
                    None,
                    None,
                    None,
                    None,
                ),
            )
            self._conn.execute(
                "UPDATE incubation_plans SET status='awaiting_review', "
                "effective_model=?, input_tokens=?, output_tokens=?, wall_seconds=?, "
                "cost=?, updated_at=? WHERE plan_id=?",
                (
                    effective_model,
                    usage.input_tokens,
                    usage.output_tokens,
                    usage.wall_seconds,
                    usage.cost,
                    stamp,
                    plan_id,
                ),
            )
            self._set_task_waiting_review(row, stamp)
            self._record_cycle_result(
                row, "cycle_complete_pending_review", (candidate_id,), stamp
            )
            return IncubationResult(
                self.get_plan(plan_id), self._candidate(candidate_id)
            )

    def commit_failure(
        self,
        plan_id: str,
        reason: str,
        *,
        episode_id: str | None,
        usage: IncubationUsage | None,
        model_called: bool,
        occurred_at: datetime,
        retryable: bool = False,
    ) -> IncubationPlan:
        stamp = utc_iso(occurred_at)
        with self._store._tx():
            row = self._plan_row(plan_id)
            owned = self._task_owned_by_plan(row)
            effective_reason = reason if owned else "task_changed"
            status = (
                IncubationPlanStatus.READY.value
                if retryable
                else (
                    IncubationPlanStatus.RESULT_UNKNOWN.value
                    if effective_reason == "result_unknown"
                    else IncubationPlanStatus.FAILED.value
                )
            )
            self._conn.execute(
                "UPDATE incubation_plans SET status=?, stop_reason=?, "
                "episode_id=COALESCE(episode_id, ?), model_called=?, input_tokens=?, "
                "output_tokens=?, wall_seconds=?, cost=?, updated_at=? WHERE plan_id=?",
                (
                    status,
                    None if retryable else effective_reason,
                    episode_id,
                    int(model_called),
                    usage.input_tokens if usage else None,
                    usage.output_tokens if usage else None,
                    usage.wall_seconds if usage else None,
                    usage.cost if usage else None,
                    stamp,
                    plan_id,
                ),
            )
            if not retryable:
                if owned:
                    self._restore_task_ready(row, stamp)
                self._record_cycle_result(
                    row,
                    effective_reason,
                    (),
                    stamp,
                    unknown=effective_reason == "result_unknown",
                )
            return self.get_plan(plan_id)

    def record_outcome(
        self,
        *,
        command_id: str,
        candidate_id: str,
        outcome: str,
        reason: str | None,
        occurred_at: datetime,
    ) -> IncubationCandidate:
        if outcome not in {"adopted", "dismissed", "invalid"}:
            raise ValueError("unsupported outcome")
        if outcome == "invalid" and (reason is None or not reason.strip()):
            raise ValueError("invalid outcome requires a reason")
        stamp = utc_iso(occurred_at)
        fingerprint = digest(encode_json([candidate_id, outcome, reason]))
        failure_code: str | None = None
        result: IncubationCandidate | None = None
        with self._store._tx():
            self._claim_command_id(command_id, "outcome", fingerprint, stamp)
            previous = self._conn.execute(
                "SELECT fingerprint, candidate_id, outcome "
                "FROM incubation_outcome_commands "
                "WHERE command_id=?",
                (command_id,),
            ).fetchone()
            if previous is not None:
                if previous["fingerprint"] != fingerprint:
                    raise IncubationError("idempotency_conflict")
                if previous["outcome"] == "task_changed":
                    raise IncubationError("task_changed")
                return self._candidate(previous["candidate_id"])
            candidate = self._candidate(candidate_id)
            if candidate.status.is_terminal:
                raise IncubationError("candidate_terminal")
            row = self._plan_row(candidate.plan_id)
            if row["status"] != IncubationPlanStatus.AWAITING_REVIEW.value:
                raise IncubationError("plan_not_awaiting_review")
            if not self._task_owned_by_plan(row):
                self._conn.execute(
                    "UPDATE incubation_candidates SET status='expired', outcome_at=?, "
                    "outcome_reason='task_changed', expires_at=? WHERE candidate_id=?",
                    (stamp, stamp, candidate_id),
                )
                self._conn.execute(
                    "UPDATE incubation_plans SET status='stopped', "
                    "stop_reason='task_changed', updated_at=? WHERE plan_id=?",
                    (stamp, candidate.plan_id),
                )
                self._conn.execute(
                    "INSERT INTO incubation_outcome_commands VALUES (?,?,?,?,?,?)",
                    (
                        command_id,
                        fingerprint,
                        candidate_id,
                        "task_changed",
                        "task_changed",
                        stamp,
                    ),
                )
                failure_code = "task_changed"
            else:
                stop_reason = {
                    "adopted": "user_adopted",
                    "dismissed": "user_dismissed",
                    "invalid": "candidate_invalid",
                }[outcome]
                self._conn.execute(
                    "UPDATE incubation_candidates SET status=?, outcome_at=?, "
                    "outcome_reason=? WHERE candidate_id=?",
                    (outcome, stamp, reason, candidate_id),
                )
                self._conn.execute(
                    "UPDATE incubation_plans SET status='stopped', stop_reason=?, "
                    "updated_at=? WHERE plan_id=?",
                    (stop_reason, stamp, candidate.plan_id),
                )
                self._conn.execute(
                    "INSERT INTO incubation_outcome_commands VALUES (?,?,?,?,?,?)",
                    (command_id, fingerprint, candidate_id, outcome, reason, stamp),
                )
                self._restore_task_ready(row, stamp)
                self._record_outcome_events(
                    command_id, row, candidate_id, outcome, reason, stamp
                )
                result = self._candidate(candidate_id)
        if failure_code is not None:
            raise IncubationError(failure_code)
        assert result is not None
        return result

    def cleanup_artifacts(
        self,
        *,
        command_id: str,
        before: str,
        occurred_at: datetime,
    ) -> int:
        cutoff = utc_iso(parse_instant(before, "before"))
        stamp = utc_iso(occurred_at)
        fingerprint = digest(encode_json(["cleanup", cutoff]))
        with self._store._tx():
            self._claim_command_id(command_id, "cleanup", fingerprint, stamp)
            previous = self._conn.execute(
                "SELECT fingerprint, result_json FROM incubation_cleanup_commands "
                "WHERE command_id=?",
                (command_id,),
            ).fetchone()
            if previous is not None:
                if previous["fingerprint"] != fingerprint:
                    raise IncubationError("idempotency_conflict")
                return int(json.loads(previous["result_json"])["cleaned_candidates"])
            rows = self._conn.execute(
                "SELECT candidate_id FROM incubation_candidates "
                "WHERE status IN ('expired','invalid') AND created_at<? "
                "AND cleaned_at IS NULL ORDER BY candidate_id",
                (cutoff,),
            ).fetchall()
            candidate_ids = tuple(row["candidate_id"] for row in rows)
            if candidate_ids:
                placeholders = ",".join("?" for _ in candidate_ids)
                self._conn.execute(
                    f"UPDATE incubation_candidates SET proposal='', "
                    f"source_refs_json='[]', new_points_json='[]', verification='', "
                    f"uncertainty='', cleaned_at=? WHERE candidate_id IN ({placeholders})",
                    (stamp, *candidate_ids),
                )
            result = encode_json({"cleaned_candidates": len(candidate_ids)})
            self._conn.execute(
                "INSERT INTO incubation_cleanup_commands VALUES (?,?,?,?,?)",
                (command_id, fingerprint, cutoff, result, stamp),
            )
            self._record_cleanup_events(
                command_id, cutoff, candidate_ids, stamp
            )
            return len(candidate_ids)

    def gate_report(self) -> IncubationGateReport:
        row = self._conn.execute(
            "SELECT COUNT(*) AS total, "
            "SUM(status='adopted') AS adopted, SUM(status='invalid') AS invalid, "
            "SUM(status='dismissed') AS dismissed "
            "FROM incubation_candidates WHERE status IN ('adopted','dismissed','invalid')"
        ).fetchone()
        total = int(row["total"])
        adopted = int(row["adopted"] or 0)
        invalid = int(row["invalid"] or 0)
        dismissed = int(row["dismissed"] or 0)
        return IncubationGateReport(
            status="reevaluation_ready" if total >= 20 else "insufficient_outcomes",
            outcome_count=total,
            adopted=adopted,
            invalid=invalid,
            dismissed=dismissed,
            adoption_rate=adopted / total if total else None,
            invalid_rate=invalid / total if total else None,
            verification_success=0,
            verification_total=0,
        )

    def consume_wake(self, observation):
        from .waking import consume_wake

        return consume_wake(self, observation)

    def wake_conditions(self):
        from .waking import wake_conditions

        return wake_conditions(self)

    def get_plan(self, plan_id: str) -> IncubationPlan:
        return self._decode_plan(self._plan_row(plan_id))

    def get_plan_for_work_item(self, work_item_id: str) -> IncubationPlan:
        row = self._conn.execute(
            "SELECT * FROM incubation_plans WHERE work_item_id=?",
            (work_item_id,),
        ).fetchone()
        if row is None:
            raise IncubationError("plan_missing")
        return self._decode_plan(row)

    def get_candidate(self, candidate_id: str) -> IncubationCandidate:
        return self._candidate(candidate_id)

    def get_candidate_for_plan(self, plan_id: str) -> IncubationCandidate | None:
        self._plan_row(plan_id)
        return self._candidate_for_plan(plan_id)

    def mark_candidate_shown(
        self, plan_id: str, *, occurred_at: datetime
    ) -> IncubationCandidate | None:
        stamp = utc_iso(occurred_at)
        with self._store._tx():
            candidate = self._candidate_for_plan(plan_id)
            if candidate is None:
                return None
            if candidate.status is CandidateStatus.NEW:
                self._conn.execute(
                    "UPDATE incubation_candidates SET status='shown', shown_at=? "
                    "WHERE candidate_id=? AND status='new'",
                    (stamp, candidate.candidate_id),
                )
                return self._candidate(candidate.candidate_id)
            return candidate

    def plans_for_reconcile(self) -> tuple[dict[str, object], ...]:
        rows = self._conn.execute(
            "SELECT * FROM incubation_plans WHERE status IN ('running','ready') "
            "OR broker_settled=0 "
            "ORDER BY created_at, plan_id"
        ).fetchall()
        return tuple(dict(row) for row in rows)

    def mark_broker_settled(self, plan_id: str) -> None:
        with self._store._tx():
            self._plan_row(plan_id)
            self._conn.execute(
                "UPDATE incubation_plans SET broker_settled=1 WHERE plan_id=?",
                (plan_id,),
            )

    def plan_count(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) FROM incubation_plans").fetchone()[0])

    def wake_count(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) FROM incubation_wakes").fetchone()[0])

    def candidate_count(self) -> int:
        return int(
            self._conn.execute("SELECT COUNT(*) FROM incubation_candidates").fetchone()[0]
        )

    def pending_review_candidates(
        self,
    ) -> tuple[tuple[IncubationPlan, IncubationCandidate], ...]:
        rows = self._conn.execute(
            "SELECT p.plan_id, c.candidate_id "
            "FROM incubation_plans AS p "
            "JOIN incubation_candidates AS c ON c.plan_id=p.plan_id "
            "WHERE p.status='awaiting_review' AND c.status IN ('new','shown') "
            "ORDER BY c.created_at, c.candidate_id"
        ).fetchall()
        return tuple(
            (
                self.get_plan(row["plan_id"]),
                self.get_candidate(row["candidate_id"]),
            )
            for row in rows
        )

    def _candidate_for_plan(self, plan_id: str) -> IncubationCandidate | None:
        row = self._conn.execute(
            "SELECT candidate_id FROM incubation_candidates WHERE plan_id=? AND cycle=1",
            (plan_id,),
        ).fetchone()
        return None if row is None else self._candidate(row["candidate_id"])

    def _candidate(self, candidate_id: str) -> IncubationCandidate:
        row = self._conn.execute(
            "SELECT * FROM incubation_candidates WHERE candidate_id=?",
            (candidate_id,),
        ).fetchone()
        if row is None:
            raise IncubationError("candidate_missing")
        return IncubationCandidate(
            candidate_id=row["candidate_id"],
            plan_id=row["plan_id"],
            cycle=int(row["cycle"]),
            proposal=row["proposal"],
            source_refs=tuple(json.loads(row["source_refs_json"])),
            new_points=tuple(json.loads(row["new_points_json"])),
            verification=row["verification"],
            uncertainty=row["uncertainty"],
            runtime=row["runtime"],
            effective_model=row["effective_model"],
            tier=row["tier"],
            policy_version=row["policy_version"],
            status=CandidateStatus(row["status"]),
            created_at=row["created_at"],
            shown_at=row["shown_at"],
            outcome_at=row["outcome_at"],
            outcome_reason=row["outcome_reason"],
            expires_at=row["expires_at"],
            cleaned_at=row["cleaned_at"],
        )

    def _plan_row(self, plan_id: str):
        row = self._conn.execute(
            "SELECT * FROM incubation_plans WHERE plan_id=?", (plan_id,)
        ).fetchone()
        if row is None:
            raise IncubationError("plan_missing")
        return row

    @staticmethod
    def _decode_plan(row) -> IncubationPlan:
        usage = (
            IncubationUsage(
                int(row["input_tokens"] or 0),
                int(row["output_tokens"] or 0),
                float(row["wall_seconds"] or 0),
                float(row["cost"]) if row["cost"] is not None else None,
            )
            if row["input_tokens"] is not None
            or row["output_tokens"] is not None
            or row["wall_seconds"] is not None
            or row["cost"] is not None
            else None
        )
        return IncubationPlan(
            plan_id=row["plan_id"],
            command_id=row["command_id"],
            task_id=row["task_id"],
            work_item_id=row["work_item_id"],
            prepared_snapshot_ref=decode_snapshot_ref(row["prepared_snapshot_json"]),
            unresolved_question=row["unresolved_question"],
            wake_condition=decode_wake_condition(
                row["wake_condition_json"], IncubationWakeCondition
            ),
            deadline=row["deadline"],
            budget=BudgetDimensions(**json.loads(row["budget_json"])),
            runtime=row["runtime"],
            cycle=int(row["cycle"]),
            max_scheduled_cycles=int(row["max_scheduled_cycles"]),
            status=IncubationPlanStatus(row["status"]),
            stop_reason=row["stop_reason"],
            episode_id=row["episode_id"],
            model_called=bool(row["model_called"]),
            broker_settled=bool(row["broker_settled"]),
            effective_model=row["effective_model"],
            usage=usage,
            policy_version=row["policy_version"],
            reframe_policy=row["reframe_policy"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _validate_deadline(command: CreateIncubationPlanCommand) -> None:
        if command.deadline is None:
            return
        deadline = parse_instant(command.deadline, "deadline")
        if command.occurred_at.astimezone(timezone.utc) >= deadline:
            raise IncubationError("deadline_expired")
        due_at = command.wake_condition.due_at
        if due_at is not None and parse_instant(due_at, "due_at") > deadline:
            raise IncubationError("deadline_expired")

    def _cancel_work_item_and_restore_task(self, row, stamp: str) -> None:
        snapshot = self._store.replay()
        work_item = next(
            item for item in snapshot.work_items if item.work_item_id == row["work_item_id"]
        )
        if not work_item.status.is_terminal:
            self._store._insert_event_in_tx(
                self._store._work_item_status_event(
                    work_item.work_item_id,
                    WorkItemStatus.CANCELLED,
                    row["task_id"],
                    stamp,
                )
            )
        self._restore_task_ready(row, stamp, snapshot=snapshot)

    def _fail_before_run(
        self,
        row,
        reason: str,
        stamp: str,
        *,
        restore_task: bool = True,
    ) -> None:
        self._conn.execute(
            "UPDATE incubation_plans SET status='stopped', stop_reason=?, updated_at=? "
            "WHERE plan_id=?",
            (reason, stamp, row["plan_id"]),
        )
        snapshot = self._store.replay()
        work_item = next(
            item
            for item in snapshot.work_items
            if item.work_item_id == row["work_item_id"]
        )
        if not work_item.status.is_terminal:
            self._store._insert_event_in_tx(
                self._store._work_item_status_event(
                    work_item.work_item_id,
                    WorkItemStatus.CANCELLED,
                    row["task_id"],
                    stamp,
                )
            )
        if restore_task:
            self._restore_task_ready(row, stamp, snapshot=snapshot)
        self._record_cycle_result(row, reason, (), stamp)

    def _restore_task_ready(self, row, stamp: str, *, snapshot=None) -> None:
        snapshot = snapshot or self._store.replay()
        task = next(
            (item for item in snapshot.tasks if item.task_id == row["task_id"]),
            None,
        )
        if task is None or not self._task_owned_by_plan(row, snapshot=snapshot):
            return
        if task.primary_work_item_id:
            primary = next(
                (
                    item
                    for item in snapshot.work_items
                    if item.work_item_id == task.primary_work_item_id
                ),
                None,
            )
            if primary is not None and not primary.status.is_terminal:
                self._store._insert_event_in_tx(
                    self._store._work_item_status_event(
                        primary.work_item_id,
                        WorkItemStatus.READY,
                        task.task_id,
                        stamp,
                    )
                )
        self._store._insert_event_in_tx(
            EventEnvelope(
                event_id=f"task.incubation.cleared.{row['plan_id']}",
                kind=EventKind.TASK_WAITING_CLEARED,
                occurred_at=stamp,
                source="kernel",
                provenance=Provenance.MACHINE_OBSERVATION,
                policy_version=POLICY_VERSION,
                payload={},
                work_item_id=task.primary_work_item_id,
                task_id=task.task_id,
                correlation_id=f"incubation:{row['plan_id']}:1",
            )
        )

    def _set_task_waiting_review(self, row, stamp: str) -> None:
        snapshot = self._store.replay()
        task = next(
            (item for item in snapshot.tasks if item.task_id == row["task_id"]),
            None,
        )
        if task is None or not self._task_owned_by_plan(row, snapshot=snapshot):
            raise IncubationError("task_changed")
        self._store._insert_event_in_tx(
            EventEnvelope(
                event_id=f"task.incubation.review.{row['plan_id']}",
                kind=EventKind.TASK_WAITING_SET,
                occurred_at=stamp,
                source="kernel",
                provenance=Provenance.MACHINE_OBSERVATION,
                policy_version=POLICY_VERSION,
                payload={
                    "kind": TaskStatus.WAITING_USER.value,
                    "cause": "incubation candidate awaiting user review",
                    "correlation_id": f"incubation:{row['plan_id']}:review",
                    "condition_kind": "incubation_review",
                    "target_ref": f"incubation:{row['plan_id']}",
                    "match_params": {"plan_id": row["plan_id"]},
                },
                work_item_id=task.primary_work_item_id,
                task_id=task.task_id,
                correlation_id=f"incubation:{row['plan_id']}:1",
            )
        )

    def _task_owned_by_plan(self, row, *, snapshot=None) -> bool:
        snapshot = snapshot or self._store.replay()
        task = next(
            (item for item in snapshot.tasks if item.task_id == row["task_id"]),
            None,
        )
        if task is None or task.waiting_condition is None:
            return False
        waiting = task.waiting_condition
        if task.status is TaskStatus.INCUBATING:
            return waiting.condition_id == f"incubation:{row['plan_id']}"
        if task.status is TaskStatus.WAITING_USER:
            return (
                waiting.correlation_id
                == f"incubation:{row['plan_id']}:review"
                and waiting.target_ref == f"incubation:{row['plan_id']}"
            )
        return False

    def _record_cycle_intent(self, row, stamp: str) -> None:
        self._store._insert_event_in_tx(
            EventEnvelope(
                event_id=f"event.incubation.cycle.intent.{row['plan_id']}.1",
                kind=EventKind.COMMAND_INTENT,
                occurred_at=stamp,
                source="kernel",
                provenance=Provenance.MACHINE_OBSERVATION,
                policy_version=POLICY_VERSION,
                payload={
                    "command_kind": "incubation.run_cycle",
                    "plan_id": row["plan_id"],
                    "cycle": 1,
                    "runtime": row["runtime"],
                },
                work_item_id=row["work_item_id"],
                task_id=row["task_id"],
                correlation_id=f"incubation:{row['plan_id']}:1",
            )
        )

    def _record_cycle_result(
        self,
        row,
        result_code: str,
        candidate_ids: tuple[str, ...],
        stamp: str,
        *,
        unknown: bool = False,
    ) -> None:
        work_status = (
            WorkItemStatus.DONE
            if result_code in {"no_increment", "cycle_complete_pending_review"}
            else WorkItemStatus.FAILED
        )
        snapshot = self._store.replay()
        work_item = next(
            (
                item
                for item in snapshot.work_items
                if item.work_item_id == row["work_item_id"]
            ),
            None,
        )
        if work_item is not None and not work_item.status.is_terminal:
            self._store._insert_event_in_tx(
                self._store._work_item_status_event(
                    work_item.work_item_id,
                    work_status,
                    row["task_id"],
                    stamp,
                )
            )
        self._store._insert_event_in_tx(
            EventEnvelope(
                event_id=(
                    f"event.incubation.cycle."
                    f"{'unknown' if unknown else 'result'}.{row['plan_id']}.1"
                ),
                kind=EventKind.COMMAND_UNKNOWN if unknown else EventKind.COMMAND_RESULT,
                occurred_at=stamp,
                source="kernel",
                provenance=Provenance.MACHINE_OBSERVATION,
                policy_version=POLICY_VERSION,
                payload={
                    "command_kind": "incubation.run_cycle",
                    "plan_id": row["plan_id"],
                    "cycle": 1,
                    "candidate_ids": list(candidate_ids),
                    "unknown_code" if unknown else "result_code": result_code,
                },
                work_item_id=row["work_item_id"],
                task_id=row["task_id"],
                episode_id=row["episode_id"],
                correlation_id=f"incubation:{row['plan_id']}:1",
            )
        )

    def _record_outcome_events(
        self,
        command_id: str,
        row,
        candidate_id: str,
        outcome: str,
        reason: str | None,
        stamp: str,
    ) -> None:
        token = digest(command_id)
        correlation = f"command.incubation.outcome.{token}"
        for result in (False, True):
            self._store._insert_event_in_tx(
                EventEnvelope(
                    event_id=(
                        f"event.incubation.outcome."
                        f"{'result' if result else 'intent'}.{token}"
                    ),
                    kind=EventKind.COMMAND_RESULT if result else EventKind.COMMAND_INTENT,
                    occurred_at=stamp,
                    source="kernel",
                    provenance=Provenance.USER_DECISION,
                    policy_version=POLICY_VERSION,
                    payload={
                        "command_kind": "incubation.candidate_outcome",
                        "candidate_id": candidate_id,
                        "outcome": outcome,
                        "reason": reason,
                        "result_code": outcome if result else None,
                    },
                    work_item_id=row["work_item_id"],
                    task_id=row["task_id"],
                    episode_id=row["episode_id"],
                    correlation_id=correlation,
                )
            )

    def _record_cleanup_events(
        self,
        command_id: str,
        before: str,
        candidate_ids: tuple[str, ...],
        stamp: str,
    ) -> None:
        token = digest(command_id)
        correlation = f"command.incubation.cleanup.{token}"
        for result in (False, True):
            self._store._insert_event_in_tx(
                EventEnvelope(
                    event_id=(
                        f"event.incubation.cleanup."
                        f"{'result' if result else 'intent'}.{token}"
                    ),
                    kind=EventKind.COMMAND_RESULT if result else EventKind.COMMAND_INTENT,
                    occurred_at=stamp,
                    source="kernel",
                    provenance=Provenance.USER_DECISION,
                    policy_version=POLICY_VERSION,
                    payload={
                        "command_kind": "incubation.cleanup_artifacts",
                        "before": before,
                        "candidate_ids": list(candidate_ids),
                        "result_code": "cleaned" if result else None,
                    },
                    correlation_id=correlation,
                )
            )

    def _record_create_event(
        self,
        command: CreateIncubationPlanCommand,
        plan_id: str,
        work_item_id: str,
        stamp: str,
    ) -> None:
        token = digest(command.command_id)
        for result in (False, True):
            self._store._insert_event_in_tx(
                EventEnvelope(
                    event_id=(
                        f"event.incubation.create."
                        f"{'result' if result else 'intent'}.{token}"
                    ),
                    kind=EventKind.COMMAND_RESULT if result else EventKind.COMMAND_INTENT,
                    occurred_at=stamp,
                    source="kernel",
                    provenance=Provenance.USER_DECISION,
                    policy_version=POLICY_VERSION,
                    payload={
                        "command_kind": "incubation.create_plan",
                        "plan_id": plan_id,
                        "automatic_incubation": False,
                        "result_code": "created" if result else None,
                    },
                    work_item_id=work_item_id,
                    task_id=command.task_id,
                    correlation_id=f"command.incubation.{token}",
                )
            )

    def _claim_command_id(
        self,
        command_id: str,
        command_kind: str,
        fingerprint: str,
        stamp: str,
    ) -> None:
        existing = self._conn.execute(
            "SELECT command_kind, fingerprint FROM incubation_command_ids "
            "WHERE command_id=?",
            (command_id,),
        ).fetchone()
        if existing is not None:
            if (
                existing["command_kind"] != command_kind
                or existing["fingerprint"] != fingerprint
            ):
                raise IncubationError("idempotency_conflict")
            return
        self._conn.execute(
            "INSERT INTO incubation_command_ids VALUES (?,?,?,?)",
            (command_id, command_kind, fingerprint, stamp),
        )

    def _record_control_event(
        self,
        command_id: str,
        row,
        *,
        command_kind: str,
        result_code: str,
        stamp: str,
    ) -> None:
        token = digest(command_id)
        for result in (False, True):
            self._store._insert_event_in_tx(
                EventEnvelope(
                    event_id=(
                        f"event.incubation.control."
                        f"{'result' if result else 'intent'}.{token}"
                    ),
                    kind=EventKind.COMMAND_RESULT if result else EventKind.COMMAND_INTENT,
                    occurred_at=stamp,
                    source="kernel",
                    provenance=Provenance.USER_DECISION,
                    policy_version=POLICY_VERSION,
                    payload={
                        "command_kind": command_kind,
                        "plan_id": row["plan_id"],
                        "result_code": result_code if result else None,
                    },
                    work_item_id=row["work_item_id"],
                    task_id=row["task_id"],
                    episode_id=row["episode_id"],
                    correlation_id=f"command.incubation.control.{token}",
                )
            )
