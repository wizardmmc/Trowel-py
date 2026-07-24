"""Episode 测试共享的时钟、snapshot 与运行态 builder。

``FakeClock`` 同时替换 Store 的 ISO 时钟和 ``datetime``，避免 lease 计算读取真实
时间。Task builder 走公开 foreground 命令；Task-less WorkItem 当前没有公开生命
周期命令，由专用测试 seam 写入状态事件。
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

from trowel_py.model_os import store as _store_mod
from trowel_py.model_os.store import ModelOsStore
from trowel_py.model_os.types import (
    ArtifactRef,
    Episode,
    EpisodeSnapshot,
    EventEnvelope,
    EventKind,
    Lease,
    MemoryEligibility,
    PendingDescriptor,
    Provenance,
    SessionPurpose,
    SideEffectRecord,
    SnapshotSource,
    Task,
    WaitingSubtype,
    WorkItemKind,
    WorkItemStatus,
)

# 固定 epoch 让 TTL 计算完全独立于真实时间。
_BASE_NOW = _dt.datetime(2026, 7, 21, 0, 0, 0, tzinfo=_dt.timezone.utc)


class FakeClock:
    """同时控制 Store 两种时间来源的测试时钟。"""

    def __init__(self, start: _dt.datetime = _BASE_NOW) -> None:
        self._now = start

    @classmethod
    def from_iso(cls, iso: str) -> "FakeClock":
        return cls(_dt.datetime.fromisoformat(iso))

    def advance(self, seconds: float) -> None:
        self._now = self._now + _dt.timedelta(seconds=seconds)

    def set(self, iso: str) -> None:
        self._now = _dt.datetime.fromisoformat(iso)

    def now_iso(self) -> str:
        return self._now.isoformat()

    def install(self, monkeypatch: Any) -> None:
        captured = self

        class _FakeDateTime(_dt.datetime):
            @classmethod
            def now(cls, tz: Any = None) -> _dt.datetime:  # type: ignore[override]
                moment = captured._now
                if tz is not None:
                    moment = moment.astimezone(tz)
                return moment

        monkeypatch.setattr(_store_mod, "_now_iso", self.now_iso)
        monkeypatch.setattr(_store_mod, "datetime", _FakeDateTime)


def make_pending(
    *,
    kind: WaitingSubtype = WaitingSubtype.INPUT,
    correlation_id: str = "corr-1",
    cause: str = "need user input",
    posed_at: str = "2026-07-21T00:00:05Z",
    native_generation: str | None = "gen-1",
) -> PendingDescriptor:
    return PendingDescriptor(
        kind=kind,
        native_generation=native_generation,
        correlation_id=correlation_id,
        cause=cause,
        posed_at=posed_at,
    )


def make_cooperative_snapshot(
    *,
    work_item_goal: str = "完成 Episode 的工作",
    task_constraints_ref: str | None = None,
    current_judgment: str = "进展到一半",
    completed_with_evidence: tuple[tuple[str, str], ...] = (
        ("action/read-config", "evidence/config-dump.txt"),
    ),
    side_effects: tuple[SideEffectRecord, ...] = (
        SideEffectRecord(
            action_ref="action/write-file",
            idempotency_key="se-key-1",
            outcome="done",
            evidence_ref="evidence/written.txt",
        ),
    ),
    unknowns: tuple[str, ...] = ("不确定边界的另一侧状态",),
    next_steps: tuple[str, ...] = ("核查外部动作结果",),
    artifacts: tuple[ArtifactRef, ...] = (ArtifactRef(kind="commit", ref="abc123"),),
    native_transcript_ref: str | None = "transcript://ep-1",
    journal_through_seq: int = 0,
) -> EpisodeSnapshot:
    return EpisodeSnapshot(
        work_item_goal=work_item_goal,
        task_constraints_ref=task_constraints_ref,
        current_judgment=current_judgment,
        completed_with_evidence=completed_with_evidence,
        side_effects=side_effects,
        unknowns=unknowns,
        waiting_condition=None,
        next_steps=next_steps,
        artifacts=artifacts,
        native_transcript_ref=native_transcript_ref,
        source=SnapshotSource.COOPERATIVE,
        journal_through_seq=journal_through_seq,
        base_snapshot_ref=None,
    )


def make_running_task_episode(
    store: ModelOsStore,
    *,
    owner: str = "runner-A",
    ttl_seconds: int = 300,
    goal: str = "调研一个缓存失效检测方案",
    idempotency_key: str = "ep-key-task-1",
    previous_snapshot_ref: Any = None,
) -> tuple[Episode, Lease, Task, str]:
    """走公开命令创建持有 foreground 的 Task 及 STARTING Episode。"""

    task = store.create_task_from_user_request(
        original_goal=goal, idempotency_key=f"task-{idempotency_key}"
    )
    store.promote_to_warm(task.task_id)
    store.claim_foreground(task.task_id)
    episode, lease = store.start_episode(
        work_item_id=task.primary_work_item_id,  # type: ignore[arg-type]
        owner=owner,
        ttl_seconds=ttl_seconds,
        idempotency_key=idempotency_key,
        task_id=task.task_id,
        previous_snapshot_ref=previous_snapshot_ref,
    )
    return episode, lease, task, task.primary_work_item_id  # type: ignore[return-value]


def make_running_system_episode(
    store: ModelOsStore,
    *,
    owner: str = "runner-A",
    ttl_seconds: int = 300,
    kind: WorkItemKind = WorkItemKind.DEFAULT,
    idempotency_key: str = "ep-key-sys-1",
) -> tuple[Episode, Lease, str]:
    """通过测试 seam 创建 RUNNING 的 Task-less WorkItem 及 Episode。"""

    work_item = store.create_work_item(
        kind=kind,
        owner_ref="system",
        task_id=None,
        session_purpose=SessionPurpose.DEFAULT,
        memory_eligibility=MemoryEligibility.INELIGIBLE,
    )
    # Task-less WorkItem 尚无公开生命周期命令，只能在测试中直接追加状态事件。
    store.append_event(
        EventEnvelope(
            event_id=f"wi.run.{work_item.work_item_id}",
            kind=EventKind.WORK_ITEM_STATUS_CHANGED,
            occurred_at=_store_mod._now_iso(),
            source="test",
            provenance=Provenance.MACHINE_OBSERVATION,
            policy_version="v0",
            payload={"new_status": WorkItemStatus.RUNNING.value},
            work_item_id=work_item.work_item_id,
        )
    )
    episode, lease = store.start_episode(
        work_item_id=work_item.work_item_id,
        owner=owner,
        ttl_seconds=ttl_seconds,
        idempotency_key=idempotency_key,
        task_id=None,
    )
    return episode, lease, work_item.work_item_id


def activate_episode(store: ModelOsStore, episode_id: str, lease: Lease) -> None:
    """在缺少公开绑定命令时，经 fenced 测试 seam 把 Episode 激活。"""

    with store._tx():
        store._append_fenced_event_in_tx(
            store._make_episode_event(
                EventKind.EPISODE_STATUS_CHANGED,
                episode_id,
                {"new_status": "active"},
                lease_id=lease.lease_id,
                owner=lease.owner,
                fencing_token=lease.fencing_token,
            )
        )
