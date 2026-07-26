"""显式 default pilot 的 Episode 编排。"""

from __future__ import annotations

import asyncio
import json
import math
import shutil
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from trowel_py.memory.store import MemoryStore
from trowel_py.model_os.episode_starting import StartEpisodeCommand
from trowel_py.model_os.episode_starting.models import StartStage
from trowel_py.model_os.routing import UserRoutePreference
from trowel_py.model_os.types import MemoryEligibility, SessionPurpose
from trowel_py.model_os.work_broker import UsageRecord

from .codec import parse_candidate_output
from .models import (
    DefaultWorkError,
    GenerationUsage,
    PilotResult,
    RunDefaultPilotCommand,
)
from .sampling import sample_sources
from .store import DefaultWorkRepository


def _prompt(sources) -> str:
    packet = [
        {"source_ref": source.uri_at_generation, "content": source.text}
        for source in sources
    ]
    return (
        "这是隔离的默认态候选回顾。只在给定材料之间寻找可能有用、但材料没有"
        "直接明说的新联系。不得使用工具、外部知识或执行任何动作。最多输出 2 条；"
        "没有强候选时输出空数组，不要凑数。每条来源必须来自给定 source_ref。"
        "只输出一个 JSON object，且字段严格为："
        '{"candidates":[{"idea":"...","source_refs":["memory://notes/..."],'
        '"related_question":"...","why_useful":"...",'
        '"verification":"...","uncertainty":"..."}]}。\n材料：'
        + json.dumps(packet, ensure_ascii=False, separators=(",", ":"))
    )


def _int(value: object) -> int:
    return (
        int(value)
        if isinstance(value, (int, float)) and not isinstance(value, bool)
        else 0
    )


class DefaultWorkService:
    def __init__(
        self,
        store,
        *,
        memory_root: Path,
        starter,
        router,
        broker,
    ) -> None:
        self._store = store
        self._repo = DefaultWorkRepository(store)
        self._memory = MemoryStore(memory_root)
        self._starter = starter
        self._router = router
        self._broker = broker
        self._work_root = Path(tempfile.gettempdir()) / "trowel-default-work"
        self._inflight: dict[tuple[str, ...], asyncio.Task[PilotResult]] = {}

    @property
    def repository(self) -> DefaultWorkRepository:
        return self._repo

    def reconcile(self) -> int:
        """只归约已有 durable 结果；不会在启动时发起 native generation。"""

        reconciled = 0
        for generation in self._repo.generations_for_reconcile():
            generation_id = generation["generation_id"]
            status = generation["status"]
            if status == "pending":
                episode_id = generation["episode_id"]
                if episode_id is None:
                    snapshot = self._store.read_snapshot()
                    episode = next(
                        (
                            item
                            for item in snapshot.episodes
                            if item.work_item_id == generation["work_item_id"]
                            and not item.status.is_terminal
                        ),
                        None,
                    )
                    if episode is not None:
                        episode_id = episode.episode_id
                        self._repo.attach_episode(generation_id, episode_id)
                        generation["episode_id"] = episode_id
                command_id = self._repo.first_command_id(generation_id)
                progress = (
                    self._starter.progress(f"default:{command_id}")
                    if command_id is not None
                    else None
                )
                safe_pre_model = {
                    StartStage.INTENT,
                    StartStage.OWNERSHIP_ACQUIRED,
                    StartStage.NATIVE_RESPONDED,
                    StartStage.BINDING_PERSISTED,
                }
                if episode_id is None or (
                    progress is not None and progress.stage in safe_pre_model
                ):
                    continue
                model_called = progress is None or progress.stage in {
                    StartStage.FIRST_TURN_REQUESTED,
                    StartStage.FIRST_TURN_ACCEPTED,
                    StartStage.TERMINAL,
                    StartStage.UNKNOWN,
                }
                self._repo.commit_failure(
                    generation_id,
                    "result_unknown",
                    episode_id=episode_id,
                    usage=None,
                    model_called=model_called,
                    occurred_at=datetime.now(timezone.utc),
                )
                generation["failure_reason"] = "result_unknown"
                generation["model_called"] = int(model_called)
                generation["status"] = "failed"
                status = "failed"
            if status == "terminal":
                sources = json.loads(generation["sources_json"])
                allowed_refs = {
                    item["uri_at_generation"]
                    for item in sources
                    if isinstance(item, dict)
                    and isinstance(item.get("uri_at_generation"), str)
                }
                drafts = parse_candidate_output(
                    generation["validated_output_json"], allowed_refs
                )
                self._repo.commit_success(
                    generation_id,
                    drafts,
                    episode_id=generation["episode_id"],
                    effective_model=generation["effective_model"],
                    usage=self._usage_from_generation(generation),
                    occurred_at=datetime.now(timezone.utc),
                )
                status = "succeeded"
            snapshot = self._store.read_snapshot()
            episode = snapshot.episode_by_id(generation["episode_id"])
            if episode is None:
                continue
            if episode.status.is_terminal:
                self._record_usage_and_release(
                    generation["work_item_id"],
                    self._usage_from_generation(generation),
                    generation_id,
                )
                continue
            if status == "succeeded":
                outcome = "succeeded"
            elif status == "failed":
                outcome = (
                    "cancelled"
                    if generation["failure_reason"] == "foreground_preempted"
                    else "failed"
                )
            else:
                continue
            self._store.settle_one_shot_episode(
                generation["episode_id"],
                outcome=outcome,
                reason=generation["failure_reason"],
            )
            if generation.get("model_called"):
                self._record_usage_and_release(
                    generation["work_item_id"],
                    self._usage_from_generation(generation),
                    generation_id,
                )
            else:
                self._release_work_lease(generation["work_item_id"])
            reconciled += 1
        return reconciled

    @staticmethod
    def _usage_from_generation(generation) -> GenerationUsage:
        return GenerationUsage(
            int(generation["input_tokens"] or 0),
            int(generation["output_tokens"] or 0),
            float(generation["wall_seconds"] or 0),
            float(generation["cost"]) if generation["cost"] is not None else None,
        )

    async def run(
        self,
        command: RunDefaultPilotCommand,
        *,
        occurred_at: datetime | None = None,
    ) -> PilotResult:
        """同一组显式来源在进程内共享 generation，响应取消不取消底层工作。"""

        key = tuple(sorted(command.source_refs))
        task = self._inflight.get(key)
        joined = task is not None
        if task is None:
            task = asyncio.create_task(self._run_once(command, occurred_at=occurred_at))
            self._inflight[key] = task
        try:
            result = await asyncio.shield(task)
            if joined:
                return await self._run_once(command, occurred_at=occurred_at)
            return result
        finally:
            if task.done():
                self._inflight.pop(key, None)
            else:
                asyncio.create_task(self._clear_when_done(key, task))

    async def _clear_when_done(
        self, key: tuple[str, ...], task: asyncio.Task[PilotResult]
    ) -> None:
        try:
            await task
        except BaseException:
            pass
        if self._inflight.get(key) is task:
            self._inflight.pop(key, None)

    async def _run_once(
        self,
        command: RunDefaultPilotCommand,
        *,
        occurred_at: datetime | None = None,
    ) -> PilotResult:
        now = occurred_at or datetime.now(timezone.utc)
        sources = sample_sources(self._memory, command.source_refs, occurred_at=now)
        deep = self._router.deep_candidate(command.runtime)
        if deep is None or deep.tier is None:
            raise DefaultWorkError("deep_route_unavailable")
        begun = self._repo.begin(command, sources, occurred_at=now)
        if begun.result is not None:
            self._settle_replayed(begun.result, begun.generation_id)
            return begun.result
        recoverable = self._repo.recoverable(begun.generation_id)
        if recoverable is not None:
            drafts = parse_candidate_output(
                recoverable.validated_output_json, set(command.source_refs)
            )
            result = self._repo.commit_success(
                recoverable.generation_id,
                drafts,
                episode_id=recoverable.episode_id,
                effective_model=recoverable.effective_model,
                usage=recoverable.usage,
                occurred_at=datetime.now(timezone.utc),
            )
            self._settle_replayed(result, begun.generation_id)
            return result

        workdir = self._work_root / begun.generation_id
        workdir.mkdir(parents=True, exist_ok=True)
        start_key = f"default:{command.command_id}"
        start_command = StartEpisodeCommand(
            work_item_id=begun.work_item_id,
            task_id=None,
            previous_episode_id=None,
            previous_snapshot_ref=None,
            runtime=command.runtime,
            model=deep.request_model or deep.model,
            effort=deep.effort,
            memory_enabled=False,
            profile_enabled=False,
            workdir=str(workdir),
            session_purpose=SessionPurpose.DEFAULT,
            memory_eligibility=MemoryEligibility.INELIGIBLE,
            permission=(
                "read-only" if command.runtime == "codex" else "bypassPermissions"
            ),
            idempotency_key=start_key,
            route_preference=UserRoutePreference.DEEP,
            route_input_fact_refs=tuple(source.uri_at_generation for source in sources),
            first_turn_text=_prompt(sources),
        )
        started = time.monotonic()
        text_parts: list[str] = []
        tool_seen = False
        terminal: str | None = None
        effective_model = deep.model or deep.request_model or "unknown"
        input_tokens = 0
        output_tokens = 0
        cost: float | None = None
        caught: BaseException | None = None
        try:
            async for event in self._starter.start(start_command):
                event_type = str(event.get("type", ""))
                payload = event.get("payload")
                payload = payload if isinstance(payload, dict) else event
                if event_type == "text" and isinstance(payload.get("text"), str):
                    text_parts.append(payload["text"])
                elif event_type == "tool_call":
                    tool_seen = True
                elif event_type in {"session_started", "model_changed"}:
                    model = payload.get("model")
                    if isinstance(model, str) and model.strip():
                        effective_model = model
                elif event_type in {"context_usage", "usage_updated"}:
                    usage = payload.get("usage") or payload.get("total") or payload
                    if isinstance(usage, dict):
                        input_tokens = max(
                            input_tokens,
                            _int(usage.get("input_tokens") or usage.get("inputTokens")),
                        )
                        output_tokens = max(
                            output_tokens,
                            _int(
                                usage.get("output_tokens") or usage.get("outputTokens")
                            ),
                        )
                elif event_type == "finished":
                    terminal = "finished"
                    usage = payload.get("usage")
                    if isinstance(usage, dict):
                        input_tokens = max(
                            input_tokens, _int(usage.get("input_tokens"))
                        )
                        output_tokens = max(
                            output_tokens, _int(usage.get("output_tokens"))
                        )
                    raw_cost = payload.get("total_cost_usd")
                    if isinstance(raw_cost, (int, float)) and math.isfinite(raw_cost):
                        cost = float(raw_cost)
                elif event_type in {"error", "interrupted", "session_exited"}:
                    if terminal is None:
                        terminal = event_type
        except Exception as exc:
            caught = exc

        progress = self._starter.progress(start_key)
        episode_id = progress.episode_id if progress is not None else None
        if episode_id is not None:
            self._repo.attach_episode(begun.generation_id, episode_id)
        wall = time.monotonic() - started
        usage = GenerationUsage(input_tokens, output_tokens, wall, cost)
        if caught is not None:
            reason = (
                "budget_denied"
                if "WorkBroker denied" in str(caught)
                else "runtime_terminal_failure"
            )
            await self._fail(begun.generation_id, episode_id, reason, usage)
            raise DefaultWorkError(reason) from caught
        if (
            progress is None
            or progress.stage is StartStage.UNKNOWN
            or episode_id is None
        ):
            await self._fail(begun.generation_id, episode_id, "result_unknown", usage)
            raise DefaultWorkError("result_unknown")
        if tool_seen:
            await self._fail(
                begun.generation_id, episode_id, "tool_use_rejected", usage
            )
            raise DefaultWorkError("tool_use_rejected")
        if terminal == "interrupted":
            await self._fail(
                begun.generation_id,
                episode_id,
                "foreground_preempted",
                usage,
                cancelled=True,
            )
            raise DefaultWorkError("foreground_preempted")
        if terminal != "finished" or progress.stage is not StartStage.TERMINAL:
            await self._fail(
                begun.generation_id, episode_id, "runtime_terminal_failure", usage
            )
            raise DefaultWorkError("runtime_terminal_failure")
        raw_output = "".join(text_parts)
        try:
            drafts = parse_candidate_output(raw_output, set(command.source_refs))
        except DefaultWorkError:
            await self._fail(
                begun.generation_id, episode_id, "output_schema_invalid", usage
            )
            raise
        validated_output_json = json.dumps(
            {
                "candidates": [
                    {
                        "idea": draft.content,
                        "source_refs": list(draft.source_refs),
                        "related_question": draft.related_question,
                        "why_useful": draft.why_useful,
                        "verification": draft.verification,
                        "uncertainty": draft.uncertainty,
                    }
                    for draft in drafts
                ]
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        self._repo.record_terminal_output(
            begun.generation_id,
            episode_id=episode_id,
            effective_model=effective_model,
            validated_output_json=validated_output_json,
            usage=usage,
            occurred_at=datetime.now(timezone.utc),
        )
        result = self._repo.commit_success(
            begun.generation_id,
            drafts,
            episode_id=episode_id,
            effective_model=effective_model,
            usage=usage,
            occurred_at=datetime.now(timezone.utc),
        )
        self._store.settle_one_shot_episode(episode_id, outcome="succeeded")
        self._record_usage_and_release(begun.work_item_id, usage, begun.generation_id)
        shutil.rmtree(workdir, ignore_errors=True)
        return result

    def _settle_replayed(self, result: PilotResult, observation_id: str) -> None:
        snapshot = self._store.read_snapshot()
        episode = snapshot.episode_by_id(result.episode_id)
        if episode is not None and not episode.status.is_terminal:
            self._store.settle_one_shot_episode(result.episode_id, outcome="succeeded")
        generation = self._repo._generation(result.generation_id)
        usage = self._usage_from_generation(generation)
        self._record_usage_and_release(result.work_item_id, usage, observation_id)

    async def _fail(
        self,
        generation_id: str,
        episode_id: str | None,
        reason: str,
        usage: GenerationUsage,
        *,
        cancelled: bool = False,
    ) -> None:
        generation = self._repo._generation(generation_id)
        self._repo.commit_failure(
            generation_id,
            reason,
            episode_id=episode_id,
            usage=usage,
            model_called=reason != "budget_denied",
            occurred_at=datetime.now(timezone.utc),
        )
        self._record_usage_and_release(generation["work_item_id"], usage, generation_id)
        if episode_id is not None:
            self._store.settle_one_shot_episode(
                episode_id,
                outcome="cancelled" if cancelled else "failed",
                reason=reason,
            )

    def _record_usage_and_release(
        self, work_item_id: str, usage: GenerationUsage, observation_id: str
    ) -> None:
        lease = next(
            (
                item
                for item in self._broker.active_leases()
                if item.work_item_id == work_item_id
            ),
            None,
        )
        if lease is None:
            return
        self._broker.record_usage(
            lease.lease_id,
            lease.fencing_token,
            UsageRecord(
                calls=1,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cost=usage.cost,
                wall_seconds=max(0, math.ceil(usage.wall_seconds)),
                occurred_at=datetime.now(timezone.utc).isoformat(),
                observation_id=observation_id,
            ),
        )
        self._broker.release(lease.lease_id, lease.fencing_token)

    def _release_work_lease(self, work_item_id: str) -> None:
        lease = next(
            (
                item
                for item in self._broker.active_leases()
                if item.work_item_id == work_item_id
            ),
            None,
        )
        if lease is not None:
            self._broker.release(lease.lease_id, lease.fencing_token)
