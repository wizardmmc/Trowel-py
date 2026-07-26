"""`SUSPENDED_READY` Episode 的原会话恢复编排。"""

from __future__ import annotations

from trowel_py.model_os.scheduling.journal import (
    append_schedule_terminal,
    complete_schedule_dispatch,
    record_resource_deferred,
)
from trowel_py.model_os.types import ReconcileReason
from trowel_py.model_os.work_broker import (
    ModelTier,
    WorkDenial,
    WorkKind,
    WorkRequest,
)
from trowel_py.quota.types import Provider
from trowel_py.model_os.routing.journal import route_tier_for_episode


class SuspendedEpisodeResumer:
    def __init__(
        self,
        store,
        *,
        broker,
        wake_controller,
        runtime_adapter,
        yield_coordinator,
    ) -> None:
        self._store = store
        self._broker = broker
        self._wake = wake_controller
        self._runtime = runtime_adapter
        self._yield = yield_coordinator

    async def resume(self, recorded) -> str:
        decision = recorded.decision
        episode_id = decision.target_episode_id
        task_id = decision.target_task_id
        work_item_id = decision.target_work_item_id
        if episode_id is None or task_id is None or work_item_id is None:
            raise ValueError("suspended dispatch requires complete target identity")
        binding = self._store.episode_runtime_binding(episode_id)
        if binding is None:
            append_schedule_terminal(
                self._store,
                recorded,
                unknown_code="unknown_requires_user_restart",
            )
            return "unknown_requires_user_restart"
        queued_generation = self._wake.pending_input_generation(episode_id)
        try:
            live_generation = self._runtime.current_generation(binding)
        except Exception:
            live_generation = None
        if (
            queued_generation != binding.runtime_generation
            or live_generation != binding.runtime_generation
        ):
            self._wake.take_pending_input(
                episode_id,
                runtime_generation=binding.runtime_generation,
            )
            current = self._store.read_snapshot().episode_by_id(episode_id)
            if current is not None and current.status.value == "suspended_ready":
                self._store.mark_pending_channel_lost(
                    episode_id,
                    reason=ReconcileReason.REQUIRES_USER_RESTART,
                )
            append_schedule_terminal(
                self._store,
                recorded,
                unknown_code="unknown_requires_user_restart",
            )
            return "unknown_requires_user_restart"

        snapshot = self._store.read_snapshot()
        episode = snapshot.episode_by_id(episode_id)
        ownership = (
            next(
                (
                    lease
                    for lease in snapshot.active_leases
                    if lease.resource_type == "episode_ownership"
                    and lease.resource_id == episode_id
                ),
                None,
            )
            if episode is not None
            else None
        )
        if ownership is None:
            ownership = self._store.acquire_episode_ownership(
                episode_id,
                owner="attention-scheduler",
                ttl_seconds=600,
                idempotency_key=f"resume:{recorded.decision_id}",
            )

        provider = Provider.CODEX if binding.runtime == "codex" else Provider.GLM
        work_lease = self._broker.request(
            WorkRequest(
                kind=WorkKind.FOREGROUND,
                provider=provider,
                model_tier=(
                    route_tier_for_episode(self._store, episode_id) or ModelTier.DEEP
                ),
                task_id=task_id,
                work_item_id=work_item_id,
                idempotency_key=f"resume:{recorded.decision_id}",
            )
        )
        if isinstance(work_lease, WorkDenial):
            return record_resource_deferred(
                self._store,
                recorded,
                reason=work_lease.reason.value,
            )

        try:
            self._store.claim_scheduled_foreground(
                decision_id=recorded.decision_id,
                task_id=task_id,
                episode_id=episode_id,
                expected_lease_id=ownership.lease_id,
                expected_owner=ownership.owner,
                expected_token=ownership.fencing_token,
            )
            await self._yield.resume_suspended_episode(
                episode_id=episode_id,
                ownership_lease_id=ownership.lease_id,
                ownership_owner=ownership.owner,
                ownership_token=ownership.fencing_token,
                work_lease_id=work_lease.lease_id,
            )
            self._broker.begin_call(work_lease.lease_id, work_lease.fencing_token)
            payload = self._wake.take_pending_input(
                episode_id,
                runtime_generation=binding.runtime_generation,
            )
            if payload is None:
                raise RuntimeError("pending input disappeared before runtime answer")
            await self._runtime.answer_pending(binding, payload)
        except BaseException:
            current = self._store.read_snapshot().episode_by_id(episode_id)
            if current is not None and current.status.value == "active":
                self._store.mark_resumed_pending_channel_lost(
                    episode_id,
                    reason=ReconcileReason.REQUIRES_USER_RESTART,
                )
            self._broker.release(work_lease.lease_id, work_lease.fencing_token)
            self._store.release_episode_ownership(episode_id)
            append_schedule_terminal(
                self._store,
                recorded,
                unknown_code="unknown_requires_user_restart",
            )
            return "unknown_requires_user_restart"

        complete_schedule_dispatch(
            self._store,
            recorded.decision_id,
            episode_id=episode_id,
        )
        return "runtime_accepted"
