from __future__ import annotations

import pytest

from trowel_py.codex_host.errors import ProtocolViolationError
from trowel_py.codex_host.events import CodexEventType
from trowel_py.codex_host.session import CodexSession, parse_thread_binding

from .support import binding_result, session_config


def test_session_config_is_not_ephemeral_by_default() -> None:
    assert session_config().ephemeral is False


def test_parse_thread_binding_reads_effective_facts() -> None:
    binding = parse_thread_binding(binding_result("t-7"))

    assert binding.thread_id == "t-7"
    assert binding.model == "gpt-5.6-sol"
    assert binding.model_provider == "openai"
    assert binding.cwd == "/tmp/trowel-test"
    assert binding.reasoning_effort == "high"
    assert dict(binding.sandbox) == {"mode": "read-only"}
    assert binding.approval_policy == {"policy": "never"}


def test_parse_thread_binding_normalizes_permission_facts() -> None:
    binding = parse_thread_binding(
        {
            "thread": {"id": "t-real"},
            "model": "gpt-5.6-sol",
            "modelProvider": "openai",
            "cwd": "/tmp/trowel-test",
            "sandbox": {"type": "readOnly", "networkAccess": False},
            "approvalPolicy": "on-request",
            "activePermissionProfile": {"id": ":read-only", "extends": None},
            "reasoningEffort": "high",
        }
    )

    assert binding.effective_sandbox == "read-only"
    assert binding.effective_approval == "on-request"
    assert binding.permission_profile == ":read-only"
    assert binding.network_access is False


def test_parse_thread_binding_missing_model_raises() -> None:
    result = {"thread": {"id": "t"}, "modelProvider": "openai", "cwd": "/x"}

    with pytest.raises(
        ProtocolViolationError,
        match=r"missing effective fact 'model'",
    ) as raised:
        parse_thread_binding(result)

    assert raised.value.payload == result


def test_parse_thread_binding_missing_thread_id_raises() -> None:
    result = {"model": "m", "modelProvider": "openai", "cwd": "/x"}

    with pytest.raises(
        ProtocolViolationError,
        match=r"thread/start response has no thread\.id",
    ) as raised:
        parse_thread_binding(result)

    assert raised.value.payload == result


def test_session_started_emitted_once_with_facts() -> None:
    session = CodexSession(session_config())
    session.begin_send()
    session.attach_thread_binding(binding_result("t-1"))

    first = session.emit_session_started_if_first()
    second = session.emit_session_started_if_first()

    assert first is not None
    assert first.type is CodexEventType.SESSION_STARTED
    assert first.payload["model"] == "gpt-5.6-sol"
    assert first.thread_id == "t-1"
    assert second is None
    assert session.drain() == [first]
