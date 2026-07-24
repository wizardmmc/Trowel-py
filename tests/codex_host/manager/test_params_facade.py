from __future__ import annotations

import inspect

import pytest

from trowel_py.codex_host import manager_params
from trowel_py.codex_host.manager import CodexHostManager
from trowel_py.codex_host.session import CodexSession, CodexSessionConfig


def test_manager_param_facades_delegate_at_call_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = CodexHostManager()
    session = object()
    calls: list[tuple[str, object]] = []
    sentinels = [object(), object(), object()]

    def thread_start(value: object) -> object:
        calls.append(("thread-start", value))
        return sentinels[0]

    def thread_resume(value: object) -> object:
        calls.append(("thread-resume", value))
        return sentinels[1]

    def turn_start(
        thread_id: str,
        text: str,
        *,
        model: str | None,
        effort: str | None,
    ) -> object:
        calls.append(("turn-start", (thread_id, text, model, effort)))
        return sentinels[2]

    monkeypatch.setattr(manager_params, "thread_start_params", thread_start)
    monkeypatch.setattr(manager_params, "thread_resume_params", thread_resume)
    monkeypatch.setattr(manager_params, "turn_start_params", turn_start)

    assert manager._thread_start_params(session) is sentinels[0]  # type: ignore[arg-type]  # noqa: SLF001
    assert manager._thread_resume_params(session) is sentinels[1]  # type: ignore[arg-type]  # noqa: SLF001
    assert (  # noqa: SLF001
        manager._turn_start_params("thread", "text", model="model", effort="high")
        is sentinels[2]
    )
    assert calls == [
        ("thread-start", session),
        ("thread-resume", session),
        ("turn-start", ("thread", "text", "model", "high")),
    ]


def test_manager_param_facades_keep_runtime_identity() -> None:
    expected = {
        "_thread_start_params": "(self, session: 'CodexSession') -> 'dict[str, Any]'",
        "_thread_resume_params": "(self, session: 'CodexSession') -> 'dict[str, Any]'",
        "_turn_start_params": (
            "(thread_id: 'str', text: 'str', *, model: 'str | None' = None, "
            "effort: 'str | None' = None) -> 'dict[str, Any]'"
        ),
    }
    for name, signature in expected.items():
        function = getattr(CodexHostManager, name)
        assert str(inspect.signature(function)) == signature
        assert function.__module__ == "trowel_py.codex_host.manager"
        assert function.__qualname__ == f"CodexHostManager.{name}"

    descriptor = inspect.getattr_static(CodexHostManager, "_turn_start_params")
    assert isinstance(descriptor, staticmethod)
    assert descriptor.__func__.__defaults__ is None
    assert descriptor.__func__.__kwdefaults__ == {"model": None, "effort": None}


def test_thread_start_params_preserve_full_config_and_key_order() -> None:
    session = CodexSession(
        CodexSessionConfig(
            trowel_session_id="session",
            workdir="/workspace",
            ephemeral=True,
            approval_policy="never",
            sandbox="workspace-write",
            model="model",
            developer_instructions="instructions",
        )
    )

    params = manager_params.thread_start_params(session)

    assert list(params) == [
        "cwd",
        "ephemeral",
        "approvalPolicy",
        "sandbox",
        "model",
        "developerInstructions",
    ]
    assert params == {
        "cwd": "/workspace",
        "ephemeral": True,
        "approvalPolicy": "never",
        "sandbox": "workspace-write",
        "model": "model",
        "developerInstructions": "instructions",
    }


def test_thread_resume_params_reject_missing_binding() -> None:
    session = CodexSession(
        CodexSessionConfig(
            trowel_session_id="session",
            workdir="/workspace",
        )
    )

    with pytest.raises(AssertionError):
        manager_params.thread_resume_params(session)


@pytest.mark.parametrize(
    ("model", "effort", "extra"),
    [
        (None, None, {}),
        ("model", None, {"model": "model"}),
        (None, "high", {"effort": "high"}),
        ("model", "high", {"model": "model", "effort": "high"}),
    ],
)
def test_turn_start_params_preserve_optional_fields(
    model: str | None,
    effort: str | None,
    extra: dict[str, str],
) -> None:
    params = manager_params.turn_start_params(
        "thread",
        "hello",
        model=model,
        effort=effort,
    )

    assert params == {
        "threadId": "thread",
        "input": [{"type": "text", "text": "hello", "text_elements": []}],
        **extra,
    }
    assert list(params) == ["threadId", "input", *extra]
