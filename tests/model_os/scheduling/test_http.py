from dataclasses import dataclass

from fastapi import FastAPI
from fastapi.testclient import TestClient

from trowel_py.model_os.routes import router
from trowel_py.model_os.scheduling import ScheduleAction
from trowel_py.model_os.store import ModelOsStore


@dataclass
class _Decision:
    action: ScheduleAction
    target_task_id: str


@dataclass
class _Recorded:
    decision_id: str


@dataclass
class _Outcome:
    decision: _Decision
    recorded: _Recorded
    result_code: str


class Scheduler:
    def __init__(self) -> None:
        self.foreground = []
        self.triggers = []

    async def request_foreground(self, task_id, *, idempotency_key):
        self.foreground.append((task_id, idempotency_key))
        return _Outcome(
            _Decision(ScheduleAction.DISPATCH, task_id),
            _Recorded("decision-1"),
            "configuration_required",
        )

    async def trigger(self, trigger):
        self.triggers.append(trigger)
        return _Outcome(
            _Decision(ScheduleAction.CONTINUE, "task-1"),
            _Recorded("decision-2"),
            "active_focus",
        )


def _app(store: ModelOsStore, scheduler: Scheduler) -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api/model-os")
    app.state.model_os_store = store
    app.state.model_os_attention_scheduler = scheduler
    return app


def test_priority_http_writes_user_command_and_triggers_scheduler(
    store: ModelOsStore,
) -> None:
    task = store.create_task_from_user_request(
        original_goal="匿名任务",
        idempotency_key="create-http-priority",
    )
    scheduler = Scheduler()

    response = TestClient(_app(store, scheduler)).post(
        f"/api/model-os/tasks/{task.task_id}/priority",
        json={"priority": 9, "idempotency_key": "priority-http-1"},
    )

    assert response.status_code == 200
    assert store.read_snapshot().tasks[0].priority == 9
    assert len(scheduler.triggers) == 1


def test_priority_http_retry_triggers_original_idempotent_event(
    store: ModelOsStore,
) -> None:
    task = store.create_task_from_user_request(
        original_goal="匿名任务",
        idempotency_key="create-http-priority-retry",
    )
    scheduler = Scheduler()
    client = TestClient(_app(store, scheduler))
    path = f"/api/model-os/tasks/{task.task_id}/priority"
    first = {"priority": 1, "idempotency_key": "priority-http-first"}
    second = {"priority": 2, "idempotency_key": "priority-http-second"}

    assert client.post(path, json=first).status_code == 200
    assert client.post(path, json=second).status_code == 200
    assert client.post(path, json=first).status_code == 200

    assert scheduler.triggers[0] == scheduler.triggers[2]
    assert scheduler.triggers[0] != scheduler.triggers[1]


def test_priority_http_conflict_returns_409(store: ModelOsStore) -> None:
    task = store.create_task_from_user_request(
        original_goal="匿名任务",
        idempotency_key="create-http-priority-conflict",
    )
    client = TestClient(_app(store, Scheduler()))
    path = f"/api/model-os/tasks/{task.task_id}/priority"
    body = {"priority": 1, "idempotency_key": "priority-http-conflict"}
    assert client.post(path, json=body).status_code == 200

    response = client.post(path, json={**body, "priority": 2})

    assert response.status_code == 409


def test_request_foreground_http_returns_dispatch_identity(store: ModelOsStore) -> None:
    task = store.create_task_from_user_request(
        original_goal="匿名任务",
        idempotency_key="create-http-foreground",
    )
    store.promote_to_warm(task.task_id)
    scheduler = Scheduler()

    response = TestClient(_app(store, scheduler)).post(
        f"/api/model-os/tasks/{task.task_id}/foreground",
        json={"idempotency_key": "foreground-http-1"},
    )

    assert response.status_code == 200
    assert response.json()["data"] == {
        "decision_id": "decision-1",
        "action": "dispatch",
        "target_task_id": task.task_id,
        "result_code": "configuration_required",
    }
    assert scheduler.foreground == [(task.task_id, "foreground-http-1")]
