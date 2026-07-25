from __future__ import annotations

from pathlib import Path

import pytest

from trowel_py.agent_host.hub import (
    RuntimeFrozenError,
    SessionConflictError,
    SessionHub,
    SessionOperationError,
)
from tests.agent_host.hub._support import (
    FakeCodexManager,
    cc_req,
    codex_req,
)


def test_patch_runtime_change_rejected(hub: SessionHub, workdir: Path):
    binding = hub.create(cc_req(workdir))
    with pytest.raises(RuntimeFrozenError):
        hub.patch(binding.session_id, runtime="codex")


def test_patch_same_runtime_ok(hub: SessionHub, workdir: Path):
    binding = hub.create(cc_req(workdir))
    hub.patch(binding.session_id, runtime="claude_code")


async def test_codex_model_switch_queues_valid_pair_and_auto_falls_back(
    hub: SessionHub, workdir: Path, codex_mgr: FakeCodexManager
):

    binding = hub.create(
        codex_req(workdir, model="gpt-5.6-sol", effort="ultra")
    )
    selected = await hub.update_codex_settings(
        binding.session_id, model="gpt-5.6-luna", effort="ultra"
    )
    assert selected == {
        "model": "gpt-5.6-luna",
        "effort": "medium",
        "adjusted": True,
    }
    session = codex_mgr.get_session(binding.session_id)
    assert session.next_turn_settings() == ("gpt-5.6-luna", "medium")

    # 排队中的设置尚未被 native turn 接受，公开 binding 仍保留当前值。
    assert hub.get(binding.session_id).model == "gpt-5.6-sol"


async def test_codex_model_switch_rejects_unknown_model(hub: SessionHub, workdir: Path):

    binding = hub.create(codex_req(workdir))
    with pytest.raises(SessionOperationError) as exc:
        await hub.update_codex_settings(
            binding.session_id, model="not-in-catalog", effort="low"
        )
    assert str(exc.value) == "model 'not-in-catalog' is not in the native catalog"
    assert exc.value.__cause__ is None
    assert exc.value.__context__ is None


async def test_codex_effort_only_uses_native_default_model_for_fresh_session(
    hub: SessionHub, workdir: Path, codex_mgr: FakeCodexManager
):

    binding = hub.create(codex_req(workdir, model=None, effort=None))
    selected = await hub.update_codex_settings(
        binding.session_id, model=None, effort="ultra"
    )
    assert selected == {
        "model": "gpt-5.6-sol",
        "effort": "ultra",
        "adjusted": False,
    }
    session = codex_mgr.get_session(binding.session_id)
    assert session.next_turn_settings() == ("gpt-5.6-sol", "ultra")


async def test_codex_permission_preset_switch_queues_override_and_persists(
    hub: SessionHub, workdir: Path, codex_mgr: FakeCodexManager
):
    """切到 Full access 立即持久化 requested preset，并为下一 turn 排队 override。

    requested preset 写入 binding 让 UI 立即反映用户意图；approval/sandbox
    排队到 session，在下次 turn/start 作为 sandboxPolicy/approvalPolicy 发出。
    binding.effective_sandbox 仍以原生事实为准，不在这里推断。
    """

    binding = hub.create(codex_req(workdir, permission_preset="workspace-write"))

    result = await hub.update_codex_permission(
        binding.session_id, permission_preset="danger-full-access"
    )

    assert result == {"permission_preset": "danger-full-access"}
    session = codex_mgr.get_session(binding.session_id)
    assert session.next_turn_permission_override() == (
        "never",
        "danger-full-access",
    )
    persisted = hub.get(binding.session_id)
    assert persisted is not None
    assert persisted.permission_preset == "danger-full-access"


async def test_codex_permission_preset_follow_rejected(
    hub: SessionHub, workdir: Path, codex_mgr: FakeCodexManager
):
    """PATCH 不接受 follow：它没有确定的 sticky 恢复语义，不能宣称权限已切换。

    follow 在 thread/start·resume 上是"不发送 override"，sticky turn override
    下无法撤销已生效的 Full access；PATCH 直接拒绝，避免给 UI 假成功。session
    内部 queue/apply 仍允许 ``(None, None)``——那是 session 层契约，与 PATCH 的
    对外语义分开。
    """

    binding = hub.create(codex_req(workdir, permission_preset="danger-full-access"))

    with pytest.raises(SessionOperationError):
        await hub.update_codex_permission(
            binding.session_id, permission_preset="follow"
        )

    # 拒绝路径不得留下任何副作用：session override 回退到原 config，binding 未改。
    session = codex_mgr.get_session(binding.session_id)
    assert session.next_turn_permission_override() == (
        "never",
        "danger-full-access",
    )
    persisted = hub.get(binding.session_id)
    assert persisted is not None
    assert persisted.permission_preset == "danger-full-access"


async def test_codex_permission_preset_rollback_when_store_put_fails(
    hub: SessionHub,
    workdir: Path,
    codex_mgr: FakeCodexManager,
    monkeypatch: pytest.MonkeyPatch,
):
    """``BindingStore.put`` 失败时 pending override、live config 与 binding 都不变。

    原子性要求：持久化失败必须让 PATCH 整体失败，不能留下客户端报错但内存权限
    已切换的"部分成功"。session 的 ``_pending``、``_config`` 与 binding 的
    ``permission_preset`` 必须保持调用前的值。
    """

    binding = hub.create(codex_req(workdir, permission_preset="workspace-write"))

    def boom(_binding) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(hub._store, "put", boom)  # noqa: SLF001

    with pytest.raises(OSError):
        await hub.update_codex_permission(
            binding.session_id, permission_preset="danger-full-access"
        )

    session = codex_mgr.get_session(binding.session_id)
    assert session.config.approval_policy == "on-request"
    assert session.config.sandbox == "workspace-write"
    assert session.next_turn_permission_override() == (
        "on-request",
        "workspace-write",
    )
    persisted = hub.get(binding.session_id)
    assert persisted is not None
    assert persisted.permission_preset == "workspace-write"


async def test_codex_permission_preset_rejects_cc(
    hub: SessionHub, workdir: Path
):
    """permission PATCH 与 model/effort 一样是 Codex-only。"""

    binding = hub.create(cc_req(workdir))

    with pytest.raises(SessionOperationError):
        await hub.update_codex_permission(
            binding.session_id, permission_preset="danger-full-access"
        )


async def test_codex_permission_preset_rejects_when_turn_active(
    hub: SessionHub, workdir: Path, codex_mgr: FakeCodexManager
):
    """活动 turn 期间修改 permission 与修改 model/effort 一样被拒绝。"""

    from tests.codex_host.session.support import binding_result

    binding = hub.create(codex_req(workdir))
    session = codex_mgr.get_session(binding.session_id)
    session.attach_thread_binding(binding_result())
    session.begin_send()

    with pytest.raises(SessionConflictError):
        await hub.update_codex_permission(
            binding.session_id, permission_preset="danger-full-access"
        )
