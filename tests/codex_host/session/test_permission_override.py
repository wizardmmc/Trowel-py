from __future__ import annotations

import pytest

from trowel_py.codex_host import manager_params
from trowel_py.codex_host.session import (
    CodexSession,
    CodexSessionConfig,
    TurnConflictError,
)

from .support import binding_result, running_session, session_config


def test_next_permission_override_falls_back_to_config_before_first_turn() -> None:
    """首次发送前没有暂存 override 时，回退到会话配置的 approval/sandbox。"""

    session = CodexSession(
        CodexSessionConfig(
            trowel_session_id="s1",
            workdir="/tmp/trowel-test",
            approval_policy="never",
            sandbox="danger-full-access",
        )
    )

    assert session.next_turn_permission_override() == (
        "never",
        "danger-full-access",
    )


def test_queue_permission_override_persists_until_commit() -> None:
    """暂存的 permission override 必须能被 next_turn_permission_override 读回。"""

    session = CodexSession(session_config())
    session.attach_thread_binding(binding_result())

    session.queue_permission_override(
        approval="never", sandbox="danger-full-access"
    )

    assert session.next_turn_permission_override() == (
        "never",
        "danger-full-access",
    )


def test_queue_permission_override_rejected_while_running() -> None:
    """活动 turn 期间不允许修改 permission，避免与正在使用的 sandbox 冲突。"""

    session = running_session()

    with pytest.raises(TurnConflictError):
        session.queue_permission_override(
            approval="never", sandbox="danger-full-access"
        )


def test_queue_permission_override_rejected_while_sending() -> None:
    """begin_send 到 record_turn_started 之间也拒绝修改 permission。"""

    session = CodexSession(session_config())
    session.attach_thread_binding(binding_result())
    session.begin_send()

    with pytest.raises(TurnConflictError):
        session.queue_permission_override(
            approval="never", sandbox="danger-full-access"
        )


def test_abort_send_keeps_pending_permission_override() -> None:
    """发送编排失败时保留待生效的 override，与 model/effort 行为一致。"""

    session = CodexSession(session_config())
    session.attach_thread_binding(binding_result())
    session.queue_permission_override(
        approval="never", sandbox="danger-full-access"
    )
    session.begin_send()

    session.abort_send()

    assert session.next_turn_permission_override() == (
        "never",
        "danger-full-access",
    )


def test_commit_turn_settings_clears_pending_permission_override() -> None:
    """turn/start 被接受后，已生效的 permission override 不应再次发送。"""

    session = CodexSession(session_config())
    session.attach_thread_binding(binding_result())
    session.queue_permission_override(
        approval="never", sandbox="danger-full-access"
    )

    session.commit_turn_settings(model="gpt-5.6-sol", effort="high")

    assert session.next_turn_permission_override() == (None, None)


def test_follow_preset_override_is_queueable() -> None:
    """切回 follow preset 等价于排队一个空 override，覆盖之前的非空请求。"""

    session = CodexSession(session_config())
    session.attach_thread_binding(binding_result())
    session.queue_permission_override(
        approval="never", sandbox="danger-full-access"
    )

    session.queue_permission_override(approval=None, sandbox=None)

    assert session.next_turn_permission_override() == (None, None)


def test_apply_permission_override_updates_resume_params() -> None:
    """PATCH 后 live ``CodexSession.config`` 必须立即反映新 preset。

    AIRC Critical 2 实测：原实现只更新 ``_pending`` 和持久 binding 的
    ``permission_preset``，``thread_resume_params`` 仍从 ``session.config``
    读旧值；host 重连会用旧 approval/sandbox 覆盖用户刚选的新权限。
    ``apply_permission_override`` 与 ``queue_permission_override`` 是两件事：
    queue 写 ``_pending`` 供下个 ``turn/start`` override；apply 直接改
    ``_config``，让 ``thread_resume_params`` 在重连时输出新值。
    """

    session = CodexSession(
        CodexSessionConfig(
            trowel_session_id="s1",
            workdir="/tmp/trowel-test",
            initial_thread_id="thread-existing",
            approval_policy="never",
            sandbox="danger-full-access",
        )
    )

    session.apply_permission_override(
        approval="on-request", sandbox="workspace-write"
    )

    params = manager_params.thread_resume_params(session)
    assert params["approvalPolicy"] == "on-request"
    assert params["sandbox"] == "workspace-write"


def test_apply_permission_override_follow_clears_resume_params() -> None:
    """切到 follow preset 后，重连不应再向原生发送 override。"""

    session = CodexSession(
        CodexSessionConfig(
            trowel_session_id="s1",
            workdir="/tmp/trowel-test",
            initial_thread_id="thread-existing",
            approval_policy="never",
            sandbox="danger-full-access",
        )
    )

    session.apply_permission_override(approval=None, sandbox=None)

    params = manager_params.thread_resume_params(session)
    assert "approvalPolicy" not in params
    assert "sandbox" not in params
