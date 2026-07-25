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
        approval: str | None,
        sandbox: str | None,
    ) -> object:
        calls.append(
            ("turn-start", (thread_id, text, model, effort, approval, sandbox))
        )
        return sentinels[2]

    monkeypatch.setattr(manager_params, "thread_start_params", thread_start)
    monkeypatch.setattr(manager_params, "thread_resume_params", thread_resume)
    monkeypatch.setattr(manager_params, "turn_start_params", turn_start)

    assert manager._thread_start_params(session) is sentinels[0]  # type: ignore[arg-type]  # noqa: SLF001
    assert manager._thread_resume_params(session) is sentinels[1]  # type: ignore[arg-type]  # noqa: SLF001
    assert (  # noqa: SLF001
        manager._turn_start_params(
            "thread",
            "text",
            model="model",
            effort="high",
            approval="never",
            sandbox="danger-full-access",
        )
        is sentinels[2]
    )
    assert calls == [
        ("thread-start", session),
        ("thread-resume", session),
        (
            "turn-start",
            ("thread", "text", "model", "high", "never", "danger-full-access"),
        ),
    ]


def test_manager_param_facades_keep_runtime_identity() -> None:
    expected = {
        "_thread_start_params": "(self, session: 'CodexSession') -> 'dict[str, Any]'",
        "_thread_resume_params": "(self, session: 'CodexSession') -> 'dict[str, Any]'",
        "_turn_start_params": (
            "(thread_id: 'str', text: 'str', *, model: 'str | None' = None, "
            "effort: 'str | None' = None, approval: 'str | None' = None, "
            "sandbox: 'str | None' = None) -> 'dict[str, Any]'"
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
    assert descriptor.__func__.__kwdefaults__ == {
        "model": None,
        "effort": None,
        "approval": None,
        "sandbox": None,
    }


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


def test_thread_resume_params_inherit_permission_override_and_cwd() -> None:
    """resume 必须把会话配置的 cwd/sandbox/approvalPolicy 带给原生 thread。

    Codex app-server 的 ``thread/resume`` 接受与 ``thread/start`` 相同的
    permission override（openai/codex app-server README）；请求不传 override 时
    Codex 回退默认 ``workspace-write``/``on-request``，导致原本 Full access 的
    会话恢复后 effective 权限回退。``cwd`` 同理由请求显式提供。
    """

    session = CodexSession(
        CodexSessionConfig(
            trowel_session_id="session",
            workdir="/workspace",
            initial_thread_id="thread-existing",
            approval_policy="never",
            sandbox="danger-full-access",
        )
    )

    params = manager_params.thread_resume_params(session)

    assert params["threadId"] == "thread-existing"
    assert params["cwd"] == "/workspace"
    assert params["approvalPolicy"] == "never"
    assert params["sandbox"] == "danger-full-access"


def test_thread_resume_params_omits_permission_override_when_unset() -> None:
    """``follow`` 等未指定权限的 preset 不应向原生发送 override。"""

    session = CodexSession(
        CodexSessionConfig(
            trowel_session_id="session",
            workdir="/workspace",
            initial_thread_id="thread-existing",
        )
    )

    params = manager_params.thread_resume_params(session)

    assert params["threadId"] == "thread-existing"
    assert params["cwd"] == "/workspace"
    assert "approvalPolicy" not in params
    assert "sandbox" not in params


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


def test_turn_start_params_full_access_override_uses_sandbox_policy_object() -> None:
    """turn/start 的 permission override 必须按上游 schema 发对字段名和值。

    Codex app-server v2 ``TurnStartParams``（codex-rs app-server-protocol）的
    sandbox 字段名是 ``sandboxPolicy`` 且值是 ``SandboxPolicy`` discriminated
    union 对象，不是 thread/start·resume 用的 ``sandbox: SandboxMode`` 字符串。
    ``danger-full-access`` 对应 ``{"type": "dangerFullAccess"}``，无额外字段。
    ``approvalPolicy`` 接受 ``AskForApproval`` 字符串枚举，可直接传 preset 值。
    """

    params = manager_params.turn_start_params(
        "thread",
        "hello",
        approval="never",
        sandbox="danger-full-access",
    )

    assert params["approvalPolicy"] == "never"
    assert params["sandboxPolicy"] == {"type": "dangerFullAccess"}
    assert "sandbox" not in params


def test_turn_start_params_read_only_override_carries_network_access() -> None:
    """read-only 的 SandboxPolicy 变体需要 networkAccess 字段。"""

    params = manager_params.turn_start_params(
        "thread",
        "hello",
        approval="on-request",
        sandbox="read-only",
    )

    assert params["approvalPolicy"] == "on-request"
    assert params["sandboxPolicy"] == {"type": "readOnly", "networkAccess": False}


def test_turn_start_params_workspace_write_carries_full_sandbox_policy() -> None:
    """workspace-write 必须在 turn/start 发送完整 ``SandboxPolicy`` 对象。

    AIRC 真实 app-server 实测：``dangerFullAccess`` thread 上只发
    ``approvalPolicy``（不带 ``sandboxPolicy``），native 仍沿用 thread 级
    ``dangerFullAccess``；必须显式发送 ``workspaceWrite`` policy 才真正降权。
    上游 codex-rs app-server-protocol v2 ``SandboxPolicy::WorkspaceWrite``
    四个字段都标 ``#[serde(default)]``，缺失时回退默认值；这里按父 Codex
    AIRC 实测的 ``empty writableRoots / network false / exclude flags false``
    显式提供，避免依赖服务端默认行为。
    """

    params = manager_params.turn_start_params(
        "thread",
        "hello",
        approval="on-request",
        sandbox="workspace-write",
    )

    assert params["approvalPolicy"] == "on-request"
    assert params["sandboxPolicy"] == {
        "type": "workspaceWrite",
        "writableRoots": [],
        "networkAccess": False,
        "excludeTmpdirEnvVar": False,
        "excludeSlashTmp": False,
    }
    assert "sandbox" not in params


def test_turn_start_params_omits_permission_override_when_none() -> None:
    """未指定 permission 时不向原生发送任何权限字段。"""

    params = manager_params.turn_start_params(
        "thread",
        "hello",
        approval=None,
        sandbox=None,
    )

    assert "approvalPolicy" not in params
    assert "sandboxPolicy" not in params
    assert "sandbox" not in params
