"""安全终态后的 suspend、checkpoint、close 与 reconcile 编排。"""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from typing import Any

from trowel_py.model_os.store import ModelOsStore
from trowel_py.model_os.types import (
    EpisodeStatus,
    PendingDescriptor,
    ReconcileReason,
    WaitingSubtype,
)
from trowel_py.model_os.yielding.journal import (
    append_command_terminal,
    episode,
    now_iso,
    record_boundary,
)
from trowel_py.model_os.yielding.models import (
    ReleaseWorkLease,
    TurnState,
    YieldReceipt,
)
from trowel_py.model_os.yielding.snapshot import (
    cooperative_snapshot,
    settle_parent_state,
)


class YieldFinalizer:
    def __init__(
        self,
        store: ModelOsStore,
        *,
        release_work_lease: ReleaseWorkLease,
        monotonic,
    ) -> None:
        self._store = store
        self._release_work_lease = release_work_lease
        self._monotonic = monotonic

    async def suspend_pending(
        self,
        state: TurnState,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> YieldReceipt:
        registration = state.registration
        request_id = payload.get("request_id") or payload.get("id")
        if not request_id:
            raise RuntimeError("pending runtime event has no request identity")
        kind = (
            WaitingSubtype.APPROVAL
            if event_type == "approval_request"
            else WaitingSubtype.INPUT
        )
        if state.pending_descriptor is None:
            state.pending_descriptor = PendingDescriptor(
                kind=kind,
                native_generation=registration.generation,
                correlation_id=str(request_id),
                cause=f"runtime_{kind.value}_pending",
                posed_at=now_iso(),
            )
        pending = state.pending_descriptor
        if pending.kind != kind or pending.correlation_id != str(request_id):
            raise RuntimeError("pending suspension retry changed request identity")
        current = episode(self._store, state)
        expected_status = (
            EpisodeStatus.SUSPENDED_WAITING_APPROVAL
            if kind == WaitingSubtype.APPROVAL
            else EpisodeStatus.SUSPENDED_WAITING_INPUT
        )
        if current.status == EpisodeStatus.ACTIVE:
            self._store.suspend_episode(
                registration.episode_id,
                expected_lease_id=registration.ownership_lease_id,
                expected_owner=registration.ownership_owner,
                expected_token=registration.ownership_token,
                pending=pending,
            )
        elif not (
            current.status == expected_status
            and current.pending_descriptor == pending
        ):
            raise RuntimeError(
                f"pending suspension cannot resume from {current.status.value}"
            )
        await self._release(state)
        receipt = YieldReceipt("suspended", registration.episode_id)
        state.final_receipt = receipt
        return receipt

    async def connection_lost(
        self, state: TurnState, *, generation: str
    ) -> YieldReceipt:
        registration = state.registration
        if generation != registration.generation:
            return YieldReceipt("no_action:stale_generation", registration.episode_id)
        current = episode(self._store, state)
        if current.status not in {
            EpisodeStatus.SUSPENDED_WAITING_INPUT,
            EpisodeStatus.SUSPENDED_WAITING_APPROVAL,
            EpisodeStatus.SUSPENDED_READY,
        }:
            return YieldReceipt("no_action:not_pending", current.episode_id)
        self._store.mark_pending_channel_lost(
            current.episode_id,
            reason=ReconcileReason.REQUIRES_USER_RESTART,
        )
        await self._release(state)
        receipt = YieldReceipt("unknown_requires_user_restart", current.episode_id)
        state.final_receipt = receipt
        return receipt

    async def release_turn(self, state: TurnState) -> None:
        """普通 terminal 只释放本轮 WorkLease，不结束 Episode。"""

        await self._release(state)

    async def finalize_cooperative(self, state: TurnState) -> YieldReceipt:
        registration = state.registration
        proposal = state.proposal
        assert proposal is not None
        current = episode(self._store, state)
        if current.status == EpisodeStatus.ACTIVE:
            self._store.request_yield(
                registration.episode_id,
                expected_lease_id=registration.ownership_lease_id,
                expected_owner=registration.ownership_owner,
                expected_token=registration.ownership_token,
                reason=proposal.reason,
            )
        ref = self._store.commit_checkpoint(
            registration.episode_id,
            expected_lease_id=registration.ownership_lease_id,
            expected_owner=registration.ownership_owner,
            expected_token=registration.ownership_token,
            snapshot=cooperative_snapshot(self._store, state, proposal),
            checkpoint_key=f"yield:{registration.episode_id}:{registration.turn_id}",
        )
        await self._release(state)
        current = episode(self._store, state)
        if current.status == EpisodeStatus.CHECKPOINTING:
            self._store.close_episode(
                registration.episode_id,
                expected_lease_id=registration.ownership_lease_id,
                expected_owner=registration.ownership_owner,
                expected_token=registration.ownership_token,
            )
        elif current.status != EpisodeStatus.CLOSED:
            raise RuntimeError(
                f"cooperative finalization cannot resume from {current.status.value}"
            )
        settle_parent_state(self._store, state, proposal)
        self._record_boundary(state, "cooperative")
        receipt = YieldReceipt(
            "closed", registration.episode_id, f"{ref.episode_id}#{ref.version}"
        )
        state.final_receipt = receipt
        return receipt

    async def finalize_forced(self, state: TurnState) -> YieldReceipt:
        registration = state.registration
        current = episode(self._store, state)
        if current.status == EpisodeStatus.ACTIVE:
            self._store.request_yield(
                registration.episode_id,
                expected_lease_id=registration.ownership_lease_id,
                expected_owner=registration.ownership_owner,
                expected_token=registration.ownership_token,
                reason=state.force_reason.value if state.force_reason else "forced",
            )
        ref = self._store.checkpoint_recovery_partial(
            registration.episode_id,
            expected_lease_id=registration.ownership_lease_id,
            expected_owner=registration.ownership_owner,
            expected_token=registration.ownership_token,
            reason=state.force_reason.value if state.force_reason else "forced",
            checkpoint_key=f"forced:{registration.episode_id}:{registration.turn_id}",
        )
        await self._release(state)
        current = episode(self._store, state)
        if current.status == EpisodeStatus.CHECKPOINTING:
            self._store.close_episode(
                registration.episode_id,
                expected_lease_id=registration.ownership_lease_id,
                expected_owner=registration.ownership_owner,
                expected_token=registration.ownership_token,
            )
        elif current.status != EpisodeStatus.CLOSED:
            raise RuntimeError(
                f"forced finalization cannot resume from {current.status.value}"
            )
        settle_parent_state(self._store, state, None)
        append_command_terminal(self._store, state, unknown=False)
        self._record_boundary(state, "forced")
        receipt = YieldReceipt(
            "closed", registration.episode_id, f"{ref.episode_id}#{ref.version}"
        )
        state.final_receipt = receipt
        return receipt

    async def finalize_unknown(self, state: TurnState) -> YieldReceipt:
        registration = state.registration
        for item_id, tool_name in sorted(state.unresolved_tools.items()):
            self._record_unknown(
                state,
                action_ref=f"tool.{item_id}",
                idempotency_key=f"runtime-item:{item_id}",
                description=f"unresolved_{tool_name}",
            )
        for task_id in sorted(state.unresolved_subagents):
            self._record_unknown(
                state,
                action_ref=f"subagent.{task_id}",
                idempotency_key=f"runtime-subagent:{task_id}",
                description="unresolved_subagent",
            )
        self._store.checkpoint_recovery_partial(
            registration.episode_id,
            expected_lease_id=registration.ownership_lease_id,
            expected_owner=registration.ownership_owner,
            expected_token=registration.ownership_token,
            reason="unresolved_runtime_work",
            checkpoint_key=f"unknown:{registration.episode_id}:{registration.turn_id}",
        )
        await self._release(state)
        current = episode(self._store, state)
        if current.status == EpisodeStatus.CHECKPOINTING:
            self._store.require_reconcile_after_interrupt(
                registration.episode_id,
                expected_lease_id=registration.ownership_lease_id,
                expected_owner=registration.ownership_owner,
                expected_token=registration.ownership_token,
                reason="unknown_requires_reconcile",
            )
        elif current.status != EpisodeStatus.RECONCILE_REQUIRED:
            raise RuntimeError(
                f"unknown finalization cannot resume from {current.status.value}"
            )
        append_command_terminal(self._store, state, unknown=True)
        self._record_boundary(state, "reconcile_required")
        receipt = YieldReceipt("reconcile_required", registration.episode_id)
        state.final_receipt = receipt
        return receipt

    def _record_unknown(
        self,
        state: TurnState,
        *,
        action_ref: str,
        idempotency_key: str,
        description: str,
    ) -> None:
        registration = state.registration
        self._store.record_side_effect(
            registration.episode_id,
            expected_lease_id=registration.ownership_lease_id,
            expected_owner=registration.ownership_owner,
            expected_token=registration.ownership_token,
            action_ref=action_ref,
            idempotency_key=idempotency_key,
            outcome="unknown_requires_reconcile",
            description=description,
        )

    async def _release(self, state: TurnState) -> None:
        work_lease_id = state.registration.work_lease_id
        if work_lease_id is None or state.work_lease_released:
            return
        result = self._release_work_lease(work_lease_id)
        if inspect.isawaitable(result):
            await result
        state.work_lease_released = True

    def _record_boundary(self, state: TurnState, choice: str) -> None:
        if state.boundary_elapsed_ms is None:
            started = (
                state.requested_monotonic
                if state.requested_monotonic is not None
                else state.started_monotonic
            )
            state.boundary_elapsed_ms = int((self._monotonic() - started) * 1000)
        record_boundary(
            self._store,
            state,
            choice=choice,
            elapsed_ms=state.boundary_elapsed_ms,
        )
