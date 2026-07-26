"""显式单轮孵化的隔离 Episode 编排。"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import shutil
import tempfile
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from pathlib import Path

from trowel_py.model_os.episode_starting import StartEpisodeCommand
from trowel_py.model_os.episode_starting.models import StartStage
from trowel_py.model_os.routing import UserRoutePreference
from trowel_py.model_os.types import MemoryEligibility, SessionPurpose
from trowel_py.model_os.work_broker import StaleWorkLease, UsageRecord, WorkKind

from .codec import encode_json, parse_candidate_output
from .models import (
    CreateIncubationPlanCommand,
    IncubationError,
    IncubationPlanStatus,
    IncubationResult,
    IncubationUsage,
    parse_instant,
)
from .repository import IncubationRepository

logger = logging.getLogger(__name__)


def _int(value: object) -> int:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value)
    return 0


class IncubationService:
    def __init__(
        self,
        store,
        *,
        starter,
        router,
        broker,
        interrupt_runtime: Callable[[str, str | None], Awaitable[bool]] | None = None,
        reconcile_runtime: Callable[[object], str] | None = None,
        work_root: Path | None = None,
        interrupt_wait_timeout_seconds: float = 10.0,
        lease_renew_interval_seconds: float | None = None,
    ) -> None:
        self._store = store
        self._repo = IncubationRepository(store)
        self._starter = starter
        self._router = router
        self._broker = broker
        self._interrupt_runtime = interrupt_runtime
        self._reconcile_runtime = reconcile_runtime
        if interrupt_wait_timeout_seconds <= 0:
            raise ValueError("interrupt_wait_timeout_seconds must be positive")
        self._interrupt_wait_timeout_seconds = interrupt_wait_timeout_seconds
        policy = getattr(broker, "policy", None)
        lease_ttl = getattr(policy, "lease_ttl_seconds", 600)
        default_renew_interval = max(1.0, lease_ttl / 3)
        self._lease_renew_interval_seconds = (
            default_renew_interval
            if lease_renew_interval_seconds is None
            else lease_renew_interval_seconds
        )
        if self._lease_renew_interval_seconds <= 0:
            raise ValueError("lease_renew_interval_seconds must be positive")
        self._work_root = work_root or (
            Path(tempfile.gettempdir()) / "trowel-incubation-work"
        )
        self._inflight: dict[str, asyncio.Task[IncubationResult]] = {}
        self._lease_guards: dict[str, asyncio.Task[None]] = {}
        self._model_calls = 0

    @property
    def repository(self) -> IncubationRepository:
        return self._repo

    @property
    def model_calls(self) -> int:
        return self._model_calls

    def create_plan(
        self, command: CreateIncubationPlanCommand
    ):
        deep = self._router.deep_candidate(command.runtime)
        if deep is None or deep.tier is None:
            raise IncubationError("deep_route_unavailable")
        return self._repo.create_plan(command)

    def cleanup_artifacts(
        self,
        *,
        command_id: str,
        before: str,
        occurred_at: datetime,
    ) -> dict[str, int]:
        if parse_instant(before, "before") > occurred_at.astimezone(timezone.utc):
            raise ValueError("cleanup before must not be in the future")
        cleaned = self._repo.cleanup_artifacts(
            command_id=command_id,
            before=before,
            occurred_at=occurred_at,
        )
        return {
            "cleaned_candidates": cleaned,
            "recovered_expired_leases": self._broker.recover_for_cleanup(
                command_id,
                before=before,
                work_kind=WorkKind.INCUBATION,
            ),
        }

    def trigger(self, wake) -> bool:
        prefix = "incubation:"
        if not wake.condition_id.startswith(prefix):
            return False
        plan_id = wake.condition_id.removeprefix(prefix)
        self.trigger_plan(plan_id)
        return True

    def trigger_plan(self, plan_id: str) -> asyncio.Task[IncubationResult]:
        current = self._inflight.get(plan_id)
        if current is not None:
            return current
        task = asyncio.create_task(
            self.run_plan(plan_id), name=f"incubation:{plan_id}:1"
        )
        self._inflight[plan_id] = task
        task.add_done_callback(lambda done: self._task_done(plan_id, done))
        return task

    def _task_done(self, plan_id: str, task: asyncio.Task[IncubationResult]) -> None:
        if self._inflight.get(plan_id) is task:
            self._inflight.pop(plan_id, None)
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.warning("[model-os] incubation cycle failed", exc_info=True)

    async def drain(self) -> None:
        tasks = tuple(self._inflight.values())
        if tasks:
            _, pending = await asyncio.wait(
                tasks, timeout=self._interrupt_wait_timeout_seconds
            )
            if pending:
                logger.warning(
                    "[model-os] cancelling %d incubation task(s) during shutdown; "
                    "durable lease remains for reconciliation",
                    len(pending),
                )
                for task in pending:
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        guards = tuple(self._lease_guards.values())
        for guard in guards:
            guard.cancel()
        if guards:
            await asyncio.gather(*guards, return_exceptions=True)

    async def interrupt_for_foreground(
        self,
        work_item_id: str,
        interrupt_runtime: Callable[[str], Awaitable[None]],
    ) -> bool:
        """安全中断已开始的孵化，并等待 unknown 对账释放 WorkLease。"""

        plan = self._repo.get_plan_for_work_item(work_item_id)
        task = self._inflight.get(plan.plan_id)
        if plan.status is not IncubationPlanStatus.RUNNING or task is None:
            return False
        binding = None
        for _ in range(100):
            current = self._repo.get_plan(plan.plan_id)
            episode_id = current.episode_id
            if episode_id is None:
                progress = self._starter.progress(f"incubation:{plan.plan_id}:1")
                episode_id = progress.episode_id if progress is not None else None
                if episode_id is not None:
                    self._repo.attach_episode(plan.plan_id, episode_id)
            if episode_id is not None:
                binding = self._store.episode_runtime_binding(episode_id)
                if binding is not None:
                    break
            if task.done():
                return False
            await asyncio.sleep(0.01)
        if binding is None:
            return False
        await interrupt_runtime(binding.agent_session_id)
        try:
            await asyncio.wait_for(
                asyncio.shield(task), timeout=self._interrupt_wait_timeout_seconds
            )
        except asyncio.TimeoutError:
            return False
        except IncubationError:
            pass
        return not any(
            lease.work_item_id == work_item_id
            for lease in self._broker.active_leases()
        )

    async def reconcile(self) -> int:
        """提交已有 durable 输出；可能已调用模型的 generation 只记 unknown。"""

        reconciled = 0
        for row in self._repo.plans_for_reconcile():
            plan_id = str(row["plan_id"])
            self._ensure_lease_guard(plan_id)
            status = str(row["status"])
            if (
                IncubationPlanStatus(status).is_terminal
                or status == IncubationPlanStatus.AWAITING_REVIEW.value
            ):
                if not bool(row["broker_settled"]):
                    if (
                        status == IncubationPlanStatus.RESULT_UNKNOWN.value
                        and bool(row["model_called"])
                        and not await self._interrupt_unknown(
                            plan_id,
                            row.get("episode_id"),
                            str(row["work_item_id"]),
                        )
                    ):
                        continue
                    self._settle_broker(
                        plan_id,
                        str(row["work_item_id"]),
                        self._usage_from_row(row),
                        calls=1 if bool(row["model_called"]) else 0,
                    )
                    reconciled += 1
                continue
            if status == IncubationPlanStatus.READY.value:
                self.trigger_plan(plan_id)
                continue
            if status != IncubationPlanStatus.RUNNING.value:
                continue
            episode_id = row.get("episode_id")
            if not isinstance(episode_id, str):
                episode_id = self._episode_for_work_item(str(row["work_item_id"]))
                if episode_id is not None:
                    self._repo.attach_episode(plan_id, episode_id)
            validated = row.get("validated_output_json")
            if isinstance(validated, str):
                plan = self._repo.get_plan(plan_id)
                draft = parse_candidate_output(validated, self._allowed_refs(plan))
                usage = self._usage_from_row(row)
                if episode_id is not None:
                    self._settle_if_open(episode_id, "succeeded")
                self._repo.commit_generation(
                    plan_id,
                    draft,
                    effective_model=str(row.get("effective_model") or "unknown"),
                    usage=usage,
                    occurred_at=datetime.now(timezone.utc),
                )
                self._settle_broker(
                    plan_id,
                    str(row["work_item_id"]),
                    usage,
                    calls=1,
                )
                reconciled += 1
                continue
            progress = self._starter.progress(f"incubation:{plan_id}:1")
            model_called = progress is not None and progress.stage in {
                StartStage.NATIVE_REQUESTED,
                StartStage.NATIVE_RESPONDED,
                StartStage.BINDING_PERSISTED,
                StartStage.FIRST_TURN_REQUESTED,
                StartStage.FIRST_TURN_ACCEPTED,
                StartStage.TERMINAL,
                StartStage.UNKNOWN,
            }
            reason = "result_unknown" if model_called else "runtime_terminal_failure"
            if episode_id is not None and not model_called:
                self._settle_if_open(episode_id, "failed", reason)
            self._repo.commit_failure(
                plan_id,
                reason,
                episode_id=episode_id,
                usage=None,
                model_called=model_called,
                occurred_at=datetime.now(timezone.utc),
            )
            if model_called and not await self._interrupt_unknown(
                plan_id, episode_id, str(row["work_item_id"])
            ):
                reconciled += 1
                continue
            self._settle_broker(
                plan_id,
                str(row["work_item_id"]),
                self._usage_from_row(row),
                calls=1 if model_called else 0,
            )
            reconciled += 1
        return reconciled

    async def run_plan(self, plan_id: str) -> IncubationResult:
        now = datetime.now(timezone.utc)
        plan = self._repo.begin_run(plan_id, occurred_at=now)
        self._ensure_lease_guard(plan_id)
        row = self._repo._plan_row(plan_id)
        validated = row["validated_output_json"]
        if validated is not None:
            draft = parse_candidate_output(validated, self._allowed_refs(plan))
            usage = self._usage_from_row(dict(row))
            result = self._repo.commit_generation(
                plan_id,
                draft,
                effective_model=str(row["effective_model"] or "unknown"),
                usage=usage,
                occurred_at=now,
            )
            self._settle_replayed(result, usage)
            return result

        deep = self._router.deep_candidate(plan.runtime)
        if deep is None or deep.tier is None:
            self._repo.commit_failure(
                plan_id,
                "runtime_terminal_failure",
                episode_id=None,
                usage=None,
                model_called=False,
                occurred_at=now,
            )
            self._settle_broker(
                plan.plan_id,
                plan.work_item_id,
                IncubationUsage(0, 0, 0, None),
                calls=0,
            )
            raise IncubationError("deep_route_unavailable")
        workdir = self._work_root / plan.plan_id
        workdir.mkdir(parents=True, exist_ok=True)
        start_key = f"incubation:{plan.plan_id}:1"
        start_command = StartEpisodeCommand(
            work_item_id=plan.work_item_id,
            task_id=plan.task_id,
            previous_episode_id=plan.prepared_snapshot_ref.episode_id,
            previous_snapshot_ref=plan.prepared_snapshot_ref,
            runtime=plan.runtime,
            model=deep.request_model or deep.model,
            effort=deep.effort,
            memory_enabled=False,
            profile_enabled=False,
            workdir=str(workdir),
            session_purpose=SessionPurpose.INCUBATION,
            memory_eligibility=MemoryEligibility.INELIGIBLE,
            permission=(
                "read-only" if plan.runtime == "codex" else "bypassPermissions"
            ),
            idempotency_key=start_key,
            route_preference=UserRoutePreference.DEEP,
            route_input_fact_refs=tuple(sorted(self._allowed_refs(plan))),
            budget_cap=plan.budget,
            first_turn_text=self._prompt(plan),
        )
        started = time.monotonic()
        text_parts: list[str] = []
        tool_seen = False
        terminal: str | None = None
        effective_model = deep.model or deep.request_model or "unknown"
        input_tokens = 0
        output_tokens = 0
        cost: float | None = None
        model_called = False
        caught: BaseException | None = None
        try:
            async for event in self._starter.start(start_command):
                event_type = str(event.get("type", ""))
                payload = event.get("payload")
                payload = payload if isinstance(payload, dict) else event
                if not model_called and event_type in {
                    "turn_start",
                    "session_started",
                    "text",
                    "finished",
                }:
                    model_called = True
                    self._model_calls += 1
                if event_type == "text" and isinstance(payload.get("text"), str):
                    text_parts.append(payload["text"])
                elif event_type == "tool_call":
                    tool_seen = True
                elif event_type in {"session_started", "model_changed"}:
                    model = payload.get("model")
                    if isinstance(model, str) and model.strip():
                        effective_model = model
                elif event_type in {"context_usage", "usage_updated"}:
                    raw_usage = payload.get("usage") or payload.get("total") or payload
                    if isinstance(raw_usage, dict):
                        input_tokens = max(
                            input_tokens,
                            _int(raw_usage.get("input_tokens") or raw_usage.get("inputTokens")),
                        )
                        output_tokens = max(
                            output_tokens,
                            _int(raw_usage.get("output_tokens") or raw_usage.get("outputTokens")),
                        )
                elif event_type == "finished":
                    terminal = "finished"
                    raw_usage = payload.get("usage")
                    if isinstance(raw_usage, dict):
                        input_tokens = max(input_tokens, _int(raw_usage.get("input_tokens")))
                        output_tokens = max(
                            output_tokens, _int(raw_usage.get("output_tokens"))
                        )
                    raw_cost = payload.get("total_cost_usd")
                    if isinstance(raw_cost, (int, float)) and math.isfinite(raw_cost):
                        cost = float(raw_cost)
                elif event_type in {"error", "interrupted", "session_exited"}:
                    if terminal is None:
                        terminal = event_type
        except asyncio.CancelledError:
            shutil.rmtree(workdir, ignore_errors=True)
            raise
        except Exception as exc:
            caught = exc

        progress = self._starter.progress(start_key)
        episode_id = progress.episode_id if progress is not None else None
        if episode_id is not None:
            self._repo.attach_episode(plan_id, episode_id)
        usage = IncubationUsage(
            input_tokens,
            output_tokens,
            time.monotonic() - started,
            cost,
        )
        try:
            may_have_called = self._may_have_called(progress, model_called)
            if caught is not None:
                if isinstance(caught, StaleWorkLease) and not may_have_called:
                    retry = self._repo.commit_failure(
                        plan.plan_id,
                        "foreground_preempted",
                        episode_id=episode_id,
                        usage=usage,
                        model_called=False,
                        occurred_at=datetime.now(timezone.utc),
                        retryable=True,
                    )
                    self._settle_broker(
                        plan.plan_id,
                        plan.work_item_id,
                        usage,
                        calls=0,
                    )
                    return IncubationResult(
                        self._repo.get_plan(retry.plan_id), None
                    )
                reason = (
                    "budget_denied"
                    if "WorkBroker denied" in str(caught)
                    else (
                        "result_unknown"
                        if may_have_called
                        else "runtime_terminal_failure"
                    )
                )
                await self._fail(
                    plan,
                    episode_id,
                    reason,
                    usage,
                    model_called=may_have_called,
                    runtime_stopped=terminal
                    in {"finished", "interrupted", "error", "session_exited"},
                )
                raise IncubationError(reason) from caught
            if progress is None or progress.stage is StartStage.UNKNOWN or episode_id is None:
                await self._fail(
                    plan,
                    episode_id,
                    "result_unknown",
                    usage,
                    model_called=may_have_called,
                )
                raise IncubationError("result_unknown")
            if tool_seen:
                await self._fail(
                    plan,
                    episode_id,
                    "runtime_terminal_failure",
                    usage,
                    model_called=model_called,
                )
                raise IncubationError("runtime_terminal_failure")
            if terminal != "finished" or progress.stage is not StartStage.TERMINAL:
                reason = (
                    "result_unknown"
                    if terminal == "interrupted" and may_have_called
                    else "runtime_terminal_failure"
                )
                await self._fail(
                    plan,
                    episode_id,
                    reason,
                    usage,
                    model_called=may_have_called,
                    runtime_stopped=terminal
                    in {"finished", "interrupted", "error", "session_exited"},
                )
                raise IncubationError(reason)
            draft = parse_candidate_output(
                "".join(text_parts), self._allowed_refs(plan)
            )
            validated_output = encode_json(
                {
                    "proposal": draft.proposal,
                    "source_refs": draft.source_refs,
                    "new_points": draft.new_points,
                    "verification": draft.verification,
                    "uncertainty": draft.uncertainty,
                }
            )
            self._repo.record_terminal_output(
                plan_id,
                episode_id=episode_id,
                effective_model=effective_model,
                validated_output_json=validated_output,
                usage=usage,
                occurred_at=datetime.now(timezone.utc),
            )
            self._settle_if_open(episode_id, "succeeded")
            result = self._repo.commit_generation(
                plan_id,
                draft,
                effective_model=effective_model,
                usage=usage,
                occurred_at=datetime.now(timezone.utc),
            )
            self._settle_broker(
                plan.plan_id,
                plan.work_item_id,
                usage,
                calls=1 if model_called else 0,
            )
            return result
        except IncubationError as exc:
            if exc.code == "output_schema_invalid":
                await self._fail(
                    plan,
                    episode_id,
                    "output_schema_invalid",
                    usage,
                    model_called=model_called,
                )
            raise
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    async def _fail(
        self,
        plan,
        episode_id: str | None,
        reason: str,
        usage: IncubationUsage,
        *,
        model_called: bool,
        runtime_stopped: bool = False,
    ) -> None:
        if episode_id is not None and reason != "result_unknown":
            outcome = "cancelled" if reason == "budget_denied" else "failed"
            self._settle_if_open(episode_id, outcome, reason)
        self._repo.commit_failure(
            plan.plan_id,
            reason,
            episode_id=episode_id,
            usage=usage,
            model_called=model_called,
            occurred_at=datetime.now(timezone.utc),
        )
        if (
            reason == "result_unknown"
            and model_called
            and not runtime_stopped
            and not await self._interrupt_unknown(
                plan.plan_id, episode_id, plan.work_item_id
            )
        ):
            return
        self._settle_broker(
            plan.plan_id,
            plan.work_item_id,
            usage,
            calls=1 if model_called else 0,
        )

    async def _interrupt_unknown(
        self,
        plan_id: str,
        episode_id: object,
        work_item_id: str,
    ) -> bool:
        if not isinstance(episode_id, str) or self._interrupt_runtime is None:
            return False
        binding = self._store.episode_runtime_binding(episode_id)
        if binding is None:
            return False
        progress = self._starter.progress(f"incubation:{plan_id}:1")
        expected_turn_id = progress.turn_id if progress is not None else None
        try:
            confirmed = await self._interrupt_runtime(
                binding.agent_session_id, expected_turn_id
            )
        except Exception:
            if self._reconcile_runtime is not None:
                try:
                    outcome = self._reconcile_runtime(binding)
                except Exception:
                    outcome = "unknown_requires_reconcile"
                if outcome in {"already_exited", "killed_private_process_group"}:
                    return True
            logger.warning(
                "[model-os] unknown incubation interrupt failed (plan=%s)",
                plan_id,
                exc_info=True,
            )
            return False
        return bool(confirmed)

    def _ensure_lease_guard(self, plan_id: str) -> None:
        current = self._lease_guards.get(plan_id)
        if current is not None and not current.done():
            return
        guard = asyncio.create_task(
            self._guard_unsettled_lease(plan_id),
            name=f"incubation-lease:{plan_id}:1",
        )
        self._lease_guards[plan_id] = guard
        guard.add_done_callback(
            lambda done: self._lease_guard_done(plan_id, done)
        )

    def _lease_guard_done(self, plan_id: str, task: asyncio.Task[None]) -> None:
        if self._lease_guards.get(plan_id) is task:
            self._lease_guards.pop(plan_id, None)
        if task.cancelled():
            return
        try:
            task.result()
        except Exception:
            logger.warning(
                "[model-os] incubation lease guard failed (plan=%s)",
                plan_id,
                exc_info=True,
            )

    async def _guard_unsettled_lease(self, plan_id: str) -> None:
        while True:
            await asyncio.sleep(self._lease_renew_interval_seconds)
            plan = self._repo.get_plan(plan_id)
            if plan.broker_settled:
                return
            lease = next(
                (
                    item
                    for item in self._broker.active_leases()
                    if item.work_item_id == plan.work_item_id
                ),
                None,
            )
            if lease is not None:
                self._broker.renew(lease.lease_id, lease.fencing_token)
            if (
                plan.status is IncubationPlanStatus.RESULT_UNKNOWN
                and plan.model_called
                and await self._interrupt_unknown(
                    plan.plan_id, plan.episode_id, plan.work_item_id
                )
            ):
                self._settle_broker(
                    plan.plan_id,
                    plan.work_item_id,
                    plan.usage or IncubationUsage(0, 0, 0, None),
                    calls=1,
                )
                return

    def _prompt(self, plan) -> str:
        snapshot = self._store.read_episode_snapshot(plan.prepared_snapshot_ref)
        state = self._store.read_snapshot()
        task = next(item for item in state.tasks if item.task_id == plan.task_id)
        snapshot_ref = self._snapshot_source_ref(plan)
        sources = [
            {
                "source_ref": snapshot_ref,
                "kind": "prepared_snapshot",
                "content": {
                    "work_item_goal": snapshot.work_item_goal,
                    "current_judgment": snapshot.current_judgment,
                    "completed_with_evidence": snapshot.completed_with_evidence,
                    "unknowns": snapshot.unknowns,
                    "next_steps": snapshot.next_steps,
                },
            }
        ]
        sources.extend(
            {
                "source_ref": f"task:{plan.task_id}:constraint:{index}",
                "kind": "task_constraint",
                "content": constraint,
            }
            for index, constraint in enumerate(task.appended_constraints, start=1)
        )
        return (
            "这是一次隔离的单轮方案孵化。不得使用工具、外部知识或执行任何动作。"
            "只依据给定材料和未解问题，从 constraint conflict、failure mode、"
            "minimal falsifiable assumption 三个视角重新审视现有判断。最多给出 3 个"
            "相对准备快照真正改变决策或降低风险的新点；没有新点时 new_points 输出"
            "空数组，不要凑数。source_refs 只能取给定 source_ref。只输出一个 JSON "
            "object，字段严格为 proposal、source_refs、new_points、verification、"
            "uncertainty。"
            f"\n未解问题：{plan.unresolved_question}"
            "\n材料："
            + json.dumps(sources, ensure_ascii=False, separators=(",", ":"))
        )

    def _allowed_refs(self, plan) -> set[str]:
        snapshot = self._store.read_snapshot()
        task = next(item for item in snapshot.tasks if item.task_id == plan.task_id)
        refs = {self._snapshot_source_ref(plan)}
        refs.update(
            f"task:{plan.task_id}:constraint:{index}"
            for index, _ in enumerate(task.appended_constraints, start=1)
        )
        return refs

    @staticmethod
    def _snapshot_source_ref(plan) -> str:
        ref = plan.prepared_snapshot_ref
        return f"snapshot:{ref.episode_id}:{ref.version}"

    @staticmethod
    def _usage_from_row(row) -> IncubationUsage:
        return IncubationUsage(
            int(row.get("input_tokens") or 0),
            int(row.get("output_tokens") or 0),
            float(row.get("wall_seconds") or 0),
            float(row["cost"]) if row.get("cost") is not None else None,
        )

    @staticmethod
    def _may_have_called(progress, observed_call: bool) -> bool:
        return observed_call or (
            progress is not None
            and progress.stage
            in {
                StartStage.NATIVE_REQUESTED,
                StartStage.NATIVE_RESPONDED,
                StartStage.BINDING_PERSISTED,
                StartStage.FIRST_TURN_REQUESTED,
                StartStage.FIRST_TURN_ACCEPTED,
                StartStage.TERMINAL,
                StartStage.UNKNOWN,
            }
        )

    def _episode_for_work_item(self, work_item_id: str) -> str | None:
        return next(
            (
                item.episode_id
                for item in self._store.read_snapshot().episodes
                if item.work_item_id == work_item_id and not item.status.is_terminal
            ),
            None,
        )

    def _settle_if_open(
        self, episode_id: str, outcome: str, reason: str | None = None
    ) -> None:
        episode = self._store.read_snapshot().episode_by_id(episode_id)
        if episode is not None and not episode.status.is_terminal:
            self._store.settle_one_shot_episode(
                episode_id, outcome=outcome, reason=reason
            )

    def _settle_replayed(
        self, result: IncubationResult, usage: IncubationUsage
    ) -> None:
        if result.plan.episode_id is not None:
            self._settle_if_open(result.plan.episode_id, "succeeded")
        self._settle_broker(
            result.plan.plan_id,
            result.plan.work_item_id,
            usage,
            calls=1,
        )

    def _settle_broker(
        self,
        plan_id: str,
        work_item_id: str,
        usage: IncubationUsage,
        *,
        calls: int,
    ) -> None:
        self._broker.settle_incubation_usage(
            work_item_id,
            UsageRecord(
                calls=calls,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cost=usage.cost,
                wall_seconds=max(0, math.ceil(usage.wall_seconds)),
                occurred_at=datetime.now(timezone.utc).isoformat(),
                observation_id=f"{plan_id}:1",
            ),
        )
        self._repo.mark_broker_settled(plan_id)
