"""Episode yield proposal、强制换班与 runtime 终态关联。"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Mapping
from typing import Any
from dataclasses import replace

from trowel_py.model_os.store import ModelOsStore
from trowel_py.model_os.types import EpisodeStatus
from trowel_py.model_os.yielding.finalization import YieldFinalizer
from trowel_py.model_os.yielding.journal import (
    record_interrupt_intent,
    record_missing_turn,
    record_no_action,
    proposal_ref,
)
from trowel_py.model_os.yielding.models import (
    ForceYieldReason,
    InterruptRuntime,
    ReleaseWorkLease,
    SoftYieldPolicy,
    SteerRuntime,
    TurnRegistration,
    TurnState,
    YieldControlError,
    YieldProposal,
    YieldReceipt,
    YieldSuggestedState,
    YieldWaitingCondition,
)
from trowel_py.model_os.yielding.runtime_events import (
    fold_activity,
    terminal_code,
    terminal_requires_reconcile,
)
from trowel_py.model_os.yielding.soft import SoftYieldController

__all__ = [
    "ForceYieldReason",
    "SoftYieldPolicy",
    "TurnRegistration",
    "YieldControlError",
    "YieldCoordinator",
    "YieldProposal",
    "YieldReceipt",
    "YieldSuggestedState",
    "YieldWaitingCondition",
]


class YieldCoordinator:
    """按 session 串行化 yield、interrupt 与 terminal race。"""

    def __init__(
        self,
        store: ModelOsStore,
        *,
        interrupt_runtime: InterruptRuntime,
        release_work_lease: ReleaseWorkLease,
        steer_runtime: SteerRuntime | None = None,
        soft_policy: SoftYieldPolicy | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._store = store
        self._interrupt_runtime = interrupt_runtime
        self._monotonic = monotonic
        self._turns: dict[str, TurnState] = {}
        self._lock = asyncio.Lock()
        self._finalizer = YieldFinalizer(
            store,
            release_work_lease=release_work_lease,
            monotonic=monotonic,
        )
        self._soft = SoftYieldController(
            store,
            steer_runtime=steer_runtime,
            policy=soft_policy or SoftYieldPolicy(),
            monotonic=monotonic,
            lock=self._lock,
            turns=self._turns,
            defer_forced=self._defer,
            dispatch_interrupt=self._dispatch_interrupt,
        )

    async def register_turn(self, registration: TurnRegistration) -> None:
        async with self._lock:
            existing = self._turns.get(registration.session_id)
            if existing is not None and existing.terminal_type is None:
                raise YieldControlError(
                    f"session {registration.session_id!r} already has an active turn"
                )
            current = self._store.read_snapshot().episode_by_id(registration.episode_id)
            if current is None or current.status != EpisodeStatus.ACTIVE:
                raise YieldControlError("turn registration requires an ACTIVE Episode")
            self._soft.cancel(registration.session_id)
            self._turns[registration.session_id] = TurnState(
                registration=registration,
                started_monotonic=self._monotonic(),
                context_generation=registration.context_generation,
            )
            self._soft.arm_from_latest(self._turns[registration.session_id])

    async def resume_suspended_episode(
        self,
        *,
        episode_id: str,
        ownership_lease_id: str,
        ownership_owner: str,
        ownership_token: int,
        work_lease_id: str,
    ) -> None:
        async with self._lock:
            state = next(
                (
                    item
                    for item in self._turns.values()
                    if item.registration.episode_id == episode_id
                ),
                None,
            )
            if state is None or state.final_receipt is None:
                raise YieldControlError("suspended Episode has no resumable turn")
            current = self._store.read_snapshot().episode_by_id(episode_id)
            if current is None or current.status is not EpisodeStatus.ACTIVE:
                raise YieldControlError("turn resume requires an ACTIVE Episode")
            state.registration = replace(
                state.registration,
                ownership_lease_id=ownership_lease_id,
                ownership_owner=ownership_owner,
                ownership_token=ownership_token,
                work_lease_id=work_lease_id,
            )
            state.final_receipt = None
            state.pending_descriptor = None
            state.work_lease_released = False

    async def propose(self, session_id: str, proposal: YieldProposal) -> YieldReceipt:
        async with self._lock:
            state = self._active_state(session_id)
            if state.terminal_type is not None:
                return YieldReceipt(
                    "no_action:stale_turn", state.registration.episode_id
                )
            if state.proposal is not None:
                return YieldReceipt("already_registered", state.registration.episode_id)
            record_no_action(
                self._store,
                state,
                kind="yield.proposal",
                choice=proposal.suggested_task_state.value,
                reason="model_yield_proposal",
                signal_refs=[proposal_ref(proposal)],
                details={"continue_same_task": proposal.continue_same_task},
            )
            state.proposal = proposal
            self._soft.mark_proposal(state)
            state.requested_monotonic = self._monotonic()
            return YieldReceipt("registered", state.registration.episode_id)

    async def request_forced(
        self,
        session_id: str,
        reason: ForceYieldReason,
        *,
        expected_turn_id: str,
        expected_generation: str,
    ) -> YieldReceipt:
        async with self._lock:
            state = self._turns.get(session_id)
            if state is None:
                record_missing_turn(self._store, reason)
                return YieldReceipt("no_action:no_active_turn", None)
            registration = state.registration
            if (
                state.terminal_type is not None
                or registration.turn_id != expected_turn_id
                or registration.generation != expected_generation
            ):
                record_no_action(
                    self._store,
                    state,
                    kind="yield.interrupt",
                    choice="no_action",
                    reason="stale_turn",
                )
                return YieldReceipt("no_action:stale_turn", registration.episode_id)
            if state.force_reason is not None:
                return YieldReceipt("already_registered", registration.episode_id)
            self._soft.cancel(session_id)
            state.force_reason = ForceYieldReason(reason)
            state.requested_monotonic = self._monotonic()
            if state.unresolved_subagents:
                return self._defer(state, "unresolved_subagent")
            urgent = reason in {
                ForceYieldReason.RUNTIME_TIMEOUT,
                ForceYieldReason.SHUTDOWN,
            }
            if state.unresolved_tools and not urgent:
                return self._defer(state, "unresolved_tool")
            if not state.safe_interrupt_window and not urgent:
                return self._defer(state, "stream_in_flight")
            return await self._dispatch_interrupt(state)

    async def observe(
        self,
        session_id: str,
        event: Mapping[str, Any],
        *,
        generation: str,
    ) -> YieldReceipt | None:
        async with self._lock:
            state = self._turns.get(session_id)
            if state is None or generation != state.registration.generation:
                return None
            if state.final_receipt is not None:
                return state.final_receipt
            event_type = str(event.get("type", ""))
            payload = event.get("payload")
            payload = payload if isinstance(payload, Mapping) else {}
            if state.terminal_type is not None:
                if event_type in {
                    "finished",
                    "interrupted",
                    "error",
                    "session_exited",
                }:
                    return await self._finalize_terminal(state)
                return None
            item_id = event.get("item_id") or payload.get("tool_use_id")
            fold_activity(state, event_type, payload, item_id)
            soft_receipt = await self._soft.observe(state, event)
            if soft_receipt is not None:
                return soft_receipt
            if event_type in {"approval_request", "elicit_request"}:
                return await self._finalizer.suspend_pending(state, event_type, payload)
            if event_type in {"finished", "interrupted", "error", "session_exited"}:
                self._soft.cancel(session_id)
                return await self._handle_terminal(state, event_type, payload)
            if self._force_can_interrupt(state):
                await self._dispatch_interrupt(state)
            return None

    async def connection_lost(
        self, session_id: str, *, generation: str
    ) -> YieldReceipt:
        async with self._lock:
            self._soft.cancel(session_id)
            state = self._turns.get(session_id)
            if state is None:
                return YieldReceipt("no_action:no_active_turn", None)
            return await self._finalizer.connection_lost(state, generation=generation)

    def context_generation(self, session_id: str) -> int:
        return self._soft.context_generation(session_id)

    def has_registered_turn(self, session_id: str) -> bool:
        return session_id in self._turns

    async def close(self) -> None:
        await self._soft.close()

    def _active_state(self, session_id: str) -> TurnState:
        state = self._turns.get(session_id)
        if state is None:
            raise YieldControlError(f"session {session_id!r} has no active turn")
        return state

    def _defer(self, state: TurnState, reason: str) -> YieldReceipt:
        record_no_action(
            self._store,
            state,
            kind="yield.interrupt",
            choice="defer",
            reason=reason,
        )
        return YieldReceipt(f"deferred:{reason}", state.registration.episode_id)

    async def _dispatch_interrupt(self, state: TurnState) -> YieldReceipt:
        decision_id, correlation_id = record_interrupt_intent(self._store, state)
        state.interrupt_decision_id = decision_id
        state.interrupt_correlation_id = correlation_id
        state.interrupt_attempted = True
        try:
            await self._interrupt_runtime(state.registration.session_id)
        except Exception:
            from trowel_py.model_os.yielding.journal import append_command_terminal

            append_command_terminal(self._store, state, unknown=True)
            return YieldReceipt("interrupt_failed", state.registration.episode_id)
        state.interrupt_sent = True
        return YieldReceipt("interrupt_sent", state.registration.episode_id)

    def _force_can_interrupt(self, state: TurnState) -> bool:
        return bool(
            state.force_reason is not None
            and not state.interrupt_attempted
            and not state.unresolved_tools
            and not state.unresolved_subagents
            and (
                state.safe_interrupt_window
                or state.force_reason
                in {ForceYieldReason.RUNTIME_TIMEOUT, ForceYieldReason.SHUTDOWN}
            )
        )

    async def _handle_terminal(
        self,
        state: TurnState,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> YieldReceipt | None:
        state.terminal_type = terminal_code(event_type, payload)
        return await self._finalize_terminal(state)

    async def _finalize_terminal(self, state: TurnState) -> YieldReceipt | None:
        terminal_unknown = terminal_requires_reconcile(state)
        if state.proposal is None and state.force_reason is None:
            if state.soft_request_attempted and not terminal_unknown:
                state.force_reason = ForceYieldReason.CONTEXT_LIMIT
                return await self._finalizer.finalize_forced(state)
            if not terminal_unknown:
                await self._finalizer.release_turn(state)
                return None
            state.force_reason = ForceYieldReason.RUNTIME_TIMEOUT
            state.requested_monotonic = self._monotonic()
            return await self._finalizer.finalize_unknown(state)
        if terminal_unknown or state.unresolved_tools or state.unresolved_subagents:
            return await self._finalizer.finalize_unknown(state)
        if state.proposal is not None and state.force_reason is None:
            return await self._finalizer.finalize_cooperative(state)
        return await self._finalizer.finalize_forced(state)
