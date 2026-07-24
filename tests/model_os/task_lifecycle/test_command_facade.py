from __future__ import annotations

import inspect
from typing import Any

import pytest

from trowel_py.model_os import store as store_module
from trowel_py.model_os.store import ModelOsStore
from trowel_py.model_os.types import Provenance


_SIGNATURES = {
    "create_task_from_user_request": (
        "(self, *, original_goal: 'str', idempotency_key: 'str', "
        "authorization_scope: 'str' = '', priority: 'int' = 0) -> 'Task'"
    ),
    "promote_to_warm": "(self, task_id: 'str') -> 'None'",
    "demote_to_backlog": "(self, task_id: 'str') -> 'None'",
    "claim_foreground": "(self, task_id: 'str') -> 'None'",
    "release_foreground": "(self) -> 'None'",
    "set_waiting_user": (
        "(self, task_id: 'str', *, cause: 'str', correlation_id: 'str', "
        "deadline: 'str | None' = None) -> 'None'"
    ),
    "set_waiting_event": (
        "(self, task_id: 'str', *, cause: 'str', condition_kind: 'str', "
        "target_ref: 'str', match_params: 'dict[str, Any] | None' = None, "
        "deadline: 'str | None' = None) -> 'None'"
    ),
    "set_incubating": (
        "(self, task_id: 'str', *, open_question: 'str', "
        "preparation_snapshot_ref: 'str', earliest_review_at: 'str | None' = None) "
        "-> 'None'"
    ),
    "clear_waiting": "(self, task_id: 'str') -> 'None'",
    "complete_task": (
        "(self, task_id: 'str', *, confirmed_by: 'str', "
        "evidence_refs: 'tuple[str, ...]' = (), confirmation_provenance: "
        "'Provenance' = <Provenance.USER_DECISION: 'user_decision'>) -> 'None'"
    ),
    "cancel_task": "(self, task_id: 'str', *, reason: 'str') -> 'None'",
    "record_task_error": (
        "(self, task_id: 'str', *, reason: 'str', "
        "last_snapshot_ref: 'str | None' = None, "
        "last_episode_ref: 'str | None' = None, "
        "recovery_hint: 'str | None' = None) -> 'None'"
    ),
    "append_constraint": (
        "(self, task_id: 'str', constraint: 'str') -> 'None'"
    ),
    "set_warm_rank": "(self, task_id: 'str', warm_rank: 'int | None') -> 'None'",
    "change_authorization": (
        "(self, task_id: 'str', *, authorization_scope: 'str', "
        "confirmed_by: 'str') -> 'None'"
    ),
}


def test_task_command_facades_keep_public_contracts() -> None:
    for name, signature in _SIGNATURES.items():
        facade = getattr(ModelOsStore, name)
        assert str(inspect.signature(facade)) == signature
        assert facade.__module__ == store_module.__name__
        assert facade.__qualname__ == f"ModelOsStore.{name}"


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def __getattr__(self, name: str):
        def record(*args: Any, **kwargs: Any) -> None:
            self.calls.append((name, args, kwargs))

        return record


@pytest.mark.parametrize(
    ("name", "args", "kwargs"),
    [
        (
            "create_task_from_user_request",
            (),
            {
                "original_goal": "goal",
                "idempotency_key": "key",
                "authorization_scope": "scope",
                "priority": 7,
            },
        ),
        ("promote_to_warm", ("task-1",), {}),
        ("demote_to_backlog", ("task-1",), {}),
        ("claim_foreground", ("task-1",), {}),
        ("release_foreground", (), {}),
        (
            "set_waiting_user",
            ("task-1",),
            {"cause": "question", "correlation_id": "reply-1", "deadline": "soon"},
        ),
        (
            "set_waiting_event",
            ("task-1",),
            {
                "cause": "dependency",
                "condition_kind": "job",
                "target_ref": "job-1",
                "match_params": {"state": "done"},
                "deadline": "soon",
            },
        ),
        (
            "set_incubating",
            ("task-1",),
            {
                "open_question": "question",
                "preparation_snapshot_ref": "snapshot-1",
                "earliest_review_at": "later",
            },
        ),
        ("clear_waiting", ("task-1",), {}),
        (
            "complete_task",
            ("task-1",),
            {
                "confirmed_by": "user",
                "evidence_refs": ("evidence-1",),
                "confirmation_provenance": Provenance.USER_DECISION,
            },
        ),
        ("cancel_task", ("task-1",), {"reason": "obsolete"}),
        (
            "record_task_error",
            ("task-1",),
            {
                "reason": "failed",
                "last_snapshot_ref": "snapshot-1",
                "last_episode_ref": "episode-1",
                "recovery_hint": "retry",
            },
        ),
        ("append_constraint", ("task-1", "constraint"), {}),
        ("set_warm_rank", ("task-1", 2), {}),
        (
            "change_authorization",
            ("task-1",),
            {"authorization_scope": "expanded", "confirmed_by": "user"},
        ),
    ],
)
def test_task_command_facades_delegate_arguments_unchanged(
    name: str,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> None:
    store = ModelOsStore(":memory:")
    recorder = _Recorder()
    store._task_commands = recorder  # type: ignore[attr-defined]

    getattr(store, name)(*args, **kwargs)

    assert recorder.calls == [(name, args, kwargs)]
