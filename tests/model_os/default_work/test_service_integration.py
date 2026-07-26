from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from trowel_py.model_os.default_work.models import (
    DefaultWorkError,
    RunDefaultPilotCommand,
)
from trowel_py.model_os.default_work.service import DefaultWorkService
from trowel_py.model_os.episode_starting import (
    NativeSessionIdentity,
    StartEpisodeCoordinator,
    StartStage,
)
from trowel_py.model_os.routing import CognitiveRouter, RouteCandidate, RouteMode
from trowel_py.model_os.routing.config import RoutingConfig
from trowel_py.model_os.store import ModelOsStore
from trowel_py.model_os.types import EpisodeStatus, WorkItemStatus
from trowel_py.model_os.work_broker import (
    BrokerPolicy,
    BudgetDimensions,
    ModelTier,
    WorkBroker,
)


def _note(root) -> None:
    notes = root / "notes"
    notes.mkdir(parents=True)
    (notes / "recent.md").write_text(
        "---\n"
        "type: note\n"
        "title: Recent\n"
        "summary: Two checks failed together\n"
        "created: 2026-07-25\n"
        "updated: 2026-07-25\n"
        "status: active\n"
        "memory_id: stable-memory-id\n"
        "content_hash: old\n"
        "---\n"
        "The release check depends on both observations.\n",
        encoding="utf-8",
    )


class Yield:
    async def register_turn(self, registration) -> None:
        self.registration = registration


class Runtime:
    def __init__(
        self,
        runtime: str,
        *,
        tool: bool = False,
        terminal: str = "finished",
        output: str | None = None,
    ) -> None:
        self.runtime = runtime
        self.tool = tool
        self.terminal = terminal
        self.output = output
        self.first_turn_text = ""
        self.turn_count = 0

    async def start_native(self, command, episode):
        return NativeSessionIdentity(
            agent_session_id=f"agent-{episode.episode_id}",
            runtime=self.runtime,
            native_session_id=f"native-{episode.episode_id}",
            runtime_generation="generation-1",
        )

    async def persist_binding(self, identity):
        return identity

    async def refresh_identity(self, identity):
        return identity

    async def start_first_turn(self, identity, text):
        self.turn_count += 1
        self.first_turn_text = text
        accepted = "turn_start" if self.runtime == "codex" else "session_started"
        yield {
            "type": accepted,
            "turn_id": "turn-1",
            "payload": {"model": "deep-model"},
        }
        if self.tool:
            yield {"type": "tool_call", "payload": {"tool_name": "command"}}
        yield {
            "type": "text",
            "payload": {
                "text": self.output
                or '{"candidates":[{"idea":"Combine both release checks",'
                '"source_refs":["memory://notes/recent"],'
                '"related_question":"Can one gate cover both?",'
                '"why_useful":"It prevents local-only green builds",'
                '"verification":"Run a clean Linux release job",'
                '"uncertainty":"Only one release path was observed"}]}'
            },
        }
        if self.terminal == "finished":
            yield {
                "type": "finished",
                "payload": {
                    "usage": {"input_tokens": 20, "output_tokens": 10},
                    "total_cost_usd": 0.2,
                },
            }
        elif self.terminal != "missing":
            yield {"type": self.terminal, "payload": {}}

    def effective_settings(self, identity):
        return "deep-model", "high"


def _build(
    tmp_path,
    runtime: str,
    *,
    tool: bool = False,
    mode: RouteMode = RouteMode.SHADOW,
    default_calls: int = 100,
    terminal: str = "finished",
    output: str | None = None,
    fault_hook=None,
):
    root = tmp_path / "memory"
    _note(root)
    db = root / "meta" / "model-os.db"
    db.parent.mkdir(parents=True)
    broker = WorkBroker(
        db,
        policy=BrokerPolicy(
            default_cap=BudgetDimensions(calls=default_calls),
            concurrency_per_account=1,
            glm_account_order=("glm",),
            codex_account_order=("codex",),
        ),
    )
    broker.open()
    store = ModelOsStore(db)
    store.open()
    config = RoutingConfig(
        mode,
        {
            runtime: (
                RouteCandidate(ModelTier.FAST, "fast-model", "low"),
                RouteCandidate(ModelTier.DEEP, "deep-model", "high"),
            )
        },
    )
    router = CognitiveRouter(store, config)
    adapter = Runtime(runtime, tool=tool, terminal=terminal, output=output)
    starter = StartEpisodeCoordinator(
        store,
        broker=broker,
        adapter=adapter,
        yield_coordinator=Yield(),
        router=router,
        fault_hook=fault_hook,
    )
    service = DefaultWorkService(
        store,
        memory_root=root,
        starter=starter,
        router=router,
        broker=broker,
    )
    return service, store, broker, adapter


@pytest.mark.asyncio
@pytest.mark.parametrize("runtime", ["claude_code", "codex"])
async def test_real_model_os_chain_settles_one_shot_success(tmp_path, runtime) -> None:
    service, store, broker, adapter = _build(tmp_path, runtime)
    try:
        result = await service.run(
            RunDefaultPilotCommand("command-1", runtime, ("memory://notes/recent",)),
            occurred_at=datetime(2026, 7, 26, tzinfo=timezone.utc),
        )
        snapshot = store.read_snapshot()
        assert snapshot.episode_by_id(result.episode_id).status is EpisodeStatus.CLOSED
        work_item = next(
            item
            for item in snapshot.work_items
            if item.work_item_id == result.work_item_id
        )
        assert work_item.status is WorkItemStatus.DONE
        assert broker.active_leases() == ()
        assert len(result.candidates) == 1
        assert "Combine both release checks" not in adapter.first_turn_text
        assert "Two checks failed together" in adapter.first_turn_text
    finally:
        store.close()
        broker.close()


@pytest.mark.asyncio
async def test_tool_event_fails_generation_and_commits_no_candidate(tmp_path) -> None:
    service, store, broker, _ = _build(tmp_path, "codex", tool=True)
    try:
        with pytest.raises(DefaultWorkError) as raised:
            await service.run(
                RunDefaultPilotCommand(
                    "command-tool", "codex", ("memory://notes/recent",)
                ),
                occurred_at=datetime(2026, 7, 26, tzinfo=timezone.utc),
            )
        assert raised.value.code == "tool_use_rejected"
        assert service.repository.candidate_count() == 0
        assert broker.active_leases() == ()
        snapshot = store.read_snapshot()
        assert snapshot.episodes[0].status is EpisodeStatus.FAILED
        assert snapshot.work_items[0].status is WorkItemStatus.FAILED
    finally:
        store.close()
        broker.close()


@pytest.mark.asyncio
async def test_terminal_output_replay_does_not_call_native_twice(
    tmp_path, monkeypatch
) -> None:
    service, store, broker, adapter = _build(tmp_path, "codex")
    original = service.repository.commit_success

    def crash(*args, **kwargs):
        raise RuntimeError("crash after durable terminal")

    monkeypatch.setattr(service.repository, "commit_success", crash)
    command = RunDefaultPilotCommand(
        "recover-terminal", "codex", ("memory://notes/recent",)
    )
    try:
        with pytest.raises(RuntimeError, match="durable terminal"):
            await service.run(
                command,
                occurred_at=datetime(2026, 7, 26, tzinfo=timezone.utc),
            )
        first_text = adapter.first_turn_text
        monkeypatch.setattr(service.repository, "commit_success", original)
        assert service.reconcile() == 1
        result = service.repository.command_result(command.command_id)
        assert result is not None
        assert len(result.candidates) == 1
        assert adapter.first_turn_text == first_text
        assert adapter.turn_count == 1
        assert broker.active_leases() == ()
    finally:
        store.close()
        broker.close()


@pytest.mark.asyncio
async def test_concurrent_commands_share_one_native_generation(tmp_path) -> None:
    service, store, broker, adapter = _build(tmp_path, "codex")
    try:
        first, second = await asyncio.gather(
            service.run(
                RunDefaultPilotCommand(
                    "concurrent-1", "codex", ("memory://notes/recent",)
                ),
                occurred_at=datetime(2026, 7, 26, tzinfo=timezone.utc),
            ),
            service.run(
                RunDefaultPilotCommand(
                    "concurrent-2", "codex", ("memory://notes/recent",)
                ),
                occurred_at=datetime(2026, 7, 26, tzinfo=timezone.utc),
            ),
        )
        assert first.generation_id == second.generation_id
        assert first.candidate_ids == second.candidate_ids
        assert adapter.turn_count == 1
        assert service.repository.command_result("concurrent-2") == second
    finally:
        store.close()
        broker.close()


@pytest.mark.asyncio
async def test_router_off_rejects_before_workitem_broker_and_runtime(tmp_path) -> None:
    service, store, broker, adapter = _build(tmp_path, "codex", mode=RouteMode.OFF)
    try:
        with pytest.raises(DefaultWorkError) as raised:
            await service.run(
                RunDefaultPilotCommand(
                    "router-off", "codex", ("memory://notes/recent",)
                ),
                occurred_at=datetime(2026, 7, 26, tzinfo=timezone.utc),
            )
        assert raised.value.code == "deep_route_unavailable"
        assert store.read_snapshot().work_items == ()
        assert broker.active_leases() == ()
        assert adapter.turn_count == 0
    finally:
        store.close()
        broker.close()


@pytest.mark.asyncio
async def test_budget_denial_fails_one_shot_without_native_call(tmp_path) -> None:
    service, store, broker, adapter = _build(tmp_path, "codex", default_calls=0)
    try:
        with pytest.raises(DefaultWorkError) as raised:
            await service.run(
                RunDefaultPilotCommand(
                    "budget-denied", "codex", ("memory://notes/recent",)
                ),
                occurred_at=datetime(2026, 7, 26, tzinfo=timezone.utc),
            )
        assert raised.value.code == "budget_denied"
        snapshot = store.read_snapshot()
        assert snapshot.episodes[0].status is EpisodeStatus.FAILED
        assert snapshot.work_items[0].status is WorkItemStatus.FAILED
        assert broker.active_leases() == ()
        assert adapter.turn_count == 0
    finally:
        store.close()
        broker.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("terminal", "output", "code", "episode_status", "work_status"),
    [
        (
            "interrupted",
            None,
            "foreground_preempted",
            EpisodeStatus.CANCELLED,
            WorkItemStatus.CANCELLED,
        ),
        (
            "error",
            None,
            "runtime_terminal_failure",
            EpisodeStatus.FAILED,
            WorkItemStatus.FAILED,
        ),
        (
            "finished",
            "not-json",
            "output_schema_invalid",
            EpisodeStatus.FAILED,
            WorkItemStatus.FAILED,
        ),
        (
            "missing",
            None,
            "result_unknown",
            EpisodeStatus.FAILED,
            WorkItemStatus.FAILED,
        ),
    ],
)
async def test_terminal_failure_modes_settle_once(
    tmp_path, terminal, output, code, episode_status, work_status
) -> None:
    service, store, broker, _ = _build(
        tmp_path, "codex", terminal=terminal, output=output
    )
    try:
        with pytest.raises(DefaultWorkError) as raised:
            await service.run(
                RunDefaultPilotCommand(
                    f"failure-{code}", "codex", ("memory://notes/recent",)
                ),
                occurred_at=datetime(2026, 7, 26, tzinfo=timezone.utc),
            )
        assert raised.value.code == code
        snapshot = store.read_snapshot()
        assert snapshot.episodes[0].status is episode_status
        assert snapshot.work_items[0].status is work_status
        assert broker.active_leases() == ()
        assert service.repository.candidate_count() == 0
    finally:
        store.close()
        broker.close()


@pytest.mark.asyncio
async def test_candidate_commit_replay_only_finishes_settlement(
    tmp_path, monkeypatch
) -> None:
    service, store, broker, adapter = _build(tmp_path, "codex")
    original = store.settle_one_shot_episode
    crashed = False

    def crash_once(*args, **kwargs):
        nonlocal crashed
        if not crashed:
            crashed = True
            raise RuntimeError("crash before settlement")
        return original(*args, **kwargs)

    monkeypatch.setattr(store, "settle_one_shot_episode", crash_once)
    command = RunDefaultPilotCommand(
        "recover-settlement", "codex", ("memory://notes/recent",)
    )
    try:
        with pytest.raises(RuntimeError, match="before settlement"):
            await service.run(
                command,
                occurred_at=datetime(2026, 7, 26, tzinfo=timezone.utc),
            )
        first_text = adapter.first_turn_text
        with store._tx():
            store._conn.execute(
                "UPDATE leases SET expires_at='2000-01-01T00:00:00+00:00' "
                "WHERE resource_type='episode_ownership' AND released_at IS NULL"
            )
        assert service.reconcile() == 1
        result = service.repository.command_result(command.command_id)
        assert result is not None
        assert adapter.first_turn_text == first_text
        assert (
            store.read_snapshot().episode_by_id(result.episode_id).status
            is EpisodeStatus.CLOSED
        )
        assert broker.active_leases() == ()
    finally:
        store.close()
        broker.close()


@pytest.mark.asyncio
async def test_startup_reconcile_marks_uncertain_first_turn_result_unknown(
    tmp_path,
) -> None:
    class Crash(BaseException):
        pass

    def fault(stage):
        if stage is StartStage.FIRST_TURN_REQUESTED:
            raise Crash()

    service, store, broker, adapter = _build(tmp_path, "codex", fault_hook=fault)
    command = RunDefaultPilotCommand(
        "crash-first-turn", "codex", ("memory://notes/recent",)
    )
    try:
        with pytest.raises(Crash):
            await service.run(
                command,
                occurred_at=datetime(2026, 7, 26, tzinfo=timezone.utc),
            )
        assert service.reconcile() == 1
        snapshot = store.read_snapshot()
        assert snapshot.episodes[0].status is EpisodeStatus.FAILED
        assert snapshot.work_items[0].status is WorkItemStatus.FAILED
        assert adapter.turn_count == 0
        assert broker.active_leases() == ()
        with pytest.raises(DefaultWorkError) as replayed:
            await service.run(
                command,
                occurred_at=datetime(2026, 7, 26, tzinfo=timezone.utc),
            )
        assert replayed.value.code == "result_unknown"
    finally:
        store.close()
        broker.close()
