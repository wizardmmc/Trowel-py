"""上下文安全线触发的 soft request、deadline 与 compact 代际。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from trowel_py.kernel_messages import KERNEL_SOFT_YIELD_MARKER
from trowel_py.model_os.context_observer import ContextConfidence, ContextSample
from trowel_py.model_os.store import ModelOsStore
from trowel_py.model_os.yielding.context import record_context_event
from trowel_py.model_os.yielding.models import (
    ForceYieldReason,
    SoftYieldPolicy,
    SteerRuntime,
    TurnState,
    YieldReceipt,
)
from trowel_py.model_os.yielding.soft_journal import (
    append_soft_request_terminal,
    record_soft_request_intent,
    record_soft_request_superseded,
)

DeferForced = Callable[[TurnState, str], YieldReceipt]
DispatchInterrupt = Callable[[TurnState], Awaitable[YieldReceipt]]


class SoftYieldController:
    def __init__(
        self,
        store: ModelOsStore,
        *,
        steer_runtime: SteerRuntime | None,
        policy: SoftYieldPolicy,
        monotonic: Callable[[], float],
        lock: asyncio.Lock,
        turns: dict[str, TurnState],
        defer_forced: DeferForced,
        dispatch_interrupt: DispatchInterrupt,
    ) -> None:
        self._store = store
        self._steer_runtime = steer_runtime
        self._policy = policy
        self._monotonic = monotonic
        self._lock = lock
        self._turns = turns
        self._defer_forced = defer_forced
        self._dispatch_interrupt = dispatch_interrupt
        self._deadlines: dict[str, asyncio.Task[None]] = {}
        self._pending_samples: dict[str, ContextSample] = {}

    async def observe(
        self, state: TurnState, event: Mapping[str, Any]
    ) -> YieldReceipt | None:
        event_type = str(event.get("type", ""))
        terminal = event_type in {
            "finished",
            "interrupted",
            "error",
            "session_exited",
        }
        prior_sample = None
        if event_type == "tool_call":
            prior_sample = self._pending_samples.pop(
                state.registration.session_id, None
            )
        context = record_context_event(self._store, state, event)
        if context is not None and context.compacted:
            return self._supersede(state)
        if terminal:
            self._pending_samples.pop(state.registration.session_id, None)
            return None
        if prior_sample is not None and not state.soft_request_attempted:
            return await self._dispatch(state, prior_sample)
        if (
            context is None
            or context.sample is None
            or context.sample.confidence is not ContextConfidence.RELIABLE
            or not self._meets_threshold(context.sample)
            or state.soft_request_attempted
        ):
            return None
        return await self._dispatch(state, context.sample)

    def arm_from_latest(self, state: TurnState) -> None:
        registration = state.registration
        observation = self._store.read_snapshot().context_observation(
            registration.episode_id, registration.native_session_id
        )
        if observation is None:
            return
        sample = observation.latest_sample
        if (
            sample.confidence is not ContextConfidence.RELIABLE
            or not self._meets_threshold(sample)
            or state.soft_request_attempted
        ):
            return
        self._pending_samples[registration.session_id] = sample

    def mark_proposal(self, state: TurnState) -> None:
        if (
            state.soft_request_attempted
            and state.soft_request_generation == state.context_generation
        ):
            state.proposal_context_generation = state.context_generation

    def context_generation(self, session_id: str) -> int:
        state = self._turns.get(session_id)
        return state.context_generation if state is not None else 0

    def cancel(self, session_id: str) -> None:
        self._pending_samples.pop(session_id, None)
        task = self._deadlines.pop(session_id, None)
        if task is not None and task is not asyncio.current_task():
            task.cancel()

    async def close(self) -> None:
        tasks = list(self._deadlines.values())
        self._deadlines.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _dispatch(
        self, state: TurnState, sample: ContextSample
    ) -> YieldReceipt:
        assert sample.ratio is not None
        registration = state.registration
        decision_id, correlation_id = record_soft_request_intent(
            self._store,
            state,
            self._policy,
            used_tokens=sample.used_tokens,
            window_tokens=sample.effective_window_tokens,
            ratio=sample.ratio,
        )
        state.soft_request_generation = state.context_generation
        state.soft_request_decision_id = decision_id
        state.soft_request_correlation_id = correlation_id
        state.soft_request_attempted = True
        try:
            if self._steer_runtime is None:
                raise RuntimeError("soft yield steer runtime is unavailable")
            await self._steer_runtime(
                registration.session_id,
                self._request_text(state),
                expected_turn_id=registration.turn_id,
                expected_generation=registration.generation,
            )
        except Exception:
            state.soft_request_unknown = True
            append_soft_request_terminal(
                self._store, state, self._policy, unknown=True
            )
            self._schedule(state)
            return YieldReceipt("soft_request_unknown", registration.episode_id)
        state.soft_request_sent = True
        append_soft_request_terminal(
            self._store, state, self._policy, unknown=False
        )
        self._schedule(state)
        return YieldReceipt("soft_request_sent", registration.episode_id)

    def _schedule(self, state: TurnState) -> None:
        session_id = state.registration.session_id
        self.cancel(session_id)
        self._deadlines[session_id] = asyncio.create_task(
            self._wait_for_deadline(
                session_id,
                state.registration.turn_id,
                state.registration.generation,
                state.context_generation,
            ),
            name=f"yield-soft-deadline-{session_id}",
        )

    async def _wait_for_deadline(
        self,
        session_id: str,
        turn_id: str,
        generation: str,
        context_generation: int,
    ) -> None:
        try:
            await asyncio.sleep(self._policy.deadline_seconds)
            async with self._lock:
                state = self._turns.get(session_id)
                if (
                    state is None
                    or state.terminal_type is not None
                    or state.registration.turn_id != turn_id
                    or state.registration.generation != generation
                    or state.context_generation != context_generation
                    or state.force_reason is not None
                ):
                    return
                state.force_reason = ForceYieldReason.CONTEXT_LIMIT
                state.requested_monotonic = self._monotonic()
                if state.unresolved_subagents:
                    self._defer_forced(state, "unresolved_subagent")
                elif state.unresolved_tools:
                    self._defer_forced(state, "unresolved_tool")
                elif not state.safe_interrupt_window:
                    self._defer_forced(state, "stream_in_flight")
                else:
                    await self._dispatch_interrupt(state)
        except asyncio.CancelledError:
            return
        finally:
            current = self._deadlines.get(session_id)
            if current is asyncio.current_task():
                self._deadlines.pop(session_id, None)

    def _supersede(self, state: TurnState) -> YieldReceipt | None:
        if state.soft_request_generation is None:
            return None
        old_generation = state.soft_request_generation
        record_soft_request_superseded(self._store, state, self._policy)
        self.cancel(state.registration.session_id)
        if state.proposal_context_generation == old_generation:
            state.proposal = None
            state.proposal_context_generation = None
        if (
            state.force_reason is ForceYieldReason.CONTEXT_LIMIT
            and not state.interrupt_attempted
        ):
            state.force_reason = None
        state.soft_request_generation = None
        state.soft_request_decision_id = None
        state.soft_request_correlation_id = None
        state.soft_request_attempted = False
        state.soft_request_sent = False
        state.soft_request_unknown = False
        state.requested_monotonic = None
        return YieldReceipt("soft_request_superseded", state.registration.episode_id)

    def _meets_threshold(self, sample: ContextSample) -> bool:
        if sample.used_tokens is not None and sample.effective_window_tokens:
            return (
                sample.used_tokens
                >= sample.effective_window_tokens * self._policy.threshold_ratio
            )
        return bool(
            sample.ratio is not None and sample.ratio >= self._policy.threshold_ratio
        )

    @staticmethod
    def _request_text(state: TurnState) -> str:
        tool_name = (
            "mcp__trowel_model_os__yield"
            if state.registration.runtime == "claude_code"
            else "trowel_model_os.yield"
        )
        return (
            f"{KERNEL_SOFT_YIELD_MARKER}\n"
            "上下文已接近安全线。请先可靠收束当前工具和写入，再调用 "
            f"{tool_name} 提交真实工作现场，并正常结束当前 turn。"
            "不要为了交接编造完成状态。\n"
            f"context_generation={state.context_generation}"
        )
