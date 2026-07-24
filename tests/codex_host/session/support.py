from __future__ import annotations

from typing import Any

from trowel_py.codex_host.session import CodexSession, CodexSessionConfig


def session_config(session_id: str = "s1") -> CodexSessionConfig:
    return CodexSessionConfig(
        trowel_session_id=session_id,
        workdir="/tmp/trowel-test",
    )


def binding_result(thread_id: str = "t-1") -> dict[str, Any]:
    # 合成输入只服务状态机单测，不作为第三方协议形态的证据。
    return {
        "thread": {"id": thread_id},
        "model": "gpt-5.6-sol",
        "modelProvider": "openai",
        "cwd": "/tmp/trowel-test",
        "sandbox": {"mode": "read-only"},
        "approvalPolicy": {"policy": "never"},
        "serviceTier": None,
        "reasoningEffort": "high",
    }


def running_session(
    session_id: str = "s1",
    thread_id: str = "t-1",
    text: str = "hi",
) -> CodexSession:
    session = CodexSession(session_config(session_id))
    session.begin_send()
    session.attach_thread_binding(binding_result(thread_id))
    session.emit_session_started_if_first()
    session.record_turn_started("turn-1", text)
    return session
