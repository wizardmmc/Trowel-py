"""验证临时资源 owner、持久快照和关闭核验。"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import pytest

from trowel_py.resource_lifecycle import (
    OwnerScope,
    ProcessIdentity,
    ResourceRegistry,
    ResourceState,
    list_descendant_processes,
)
from trowel_py.resource_lifecycle.registry import redact_identity


class FakeProcessController:
    """为资源登记提供可控的进程启动身份。"""

    def __init__(self) -> None:
        self.identities = {
            410: ProcessIdentity(
                pid=410,
                process_group=410,
                start_identity="start-410",
            )
        }

    def inspect(self, pid: int) -> ProcessIdentity | None:
        """返回测试预置的进程身份。"""

        return self.identities.get(pid)

    def group_alive(self, process_group: int) -> bool:
        """报告预置进程组是否存在。"""

        return any(
            item.process_group == process_group for item in self.identities.values()
        )

    def signal_group(self, process_group: int, signal_name: str) -> None:
        """本测试不执行进程终止。"""

        raise AssertionError(f"unexpected signal {signal_name} to {process_group}")


def make_registry(tmp_path: Path) -> ResourceRegistry:
    """创建使用固定时钟和假进程信息的隔离 registry。"""

    return ResourceRegistry(
        app_instance_id="app-private-id",
        snapshot_path=tmp_path / "resource-lifecycle.json",
        process_controller=FakeProcessController(),
        now=lambda: datetime.fromisoformat("2026-08-01T12:00:00"),
    )


def test_owner_scope_requires_only_its_real_identity(tmp_path: Path) -> None:
    registry = make_registry(tmp_path)

    app_record = registry.register_handle(
        resource_id="sidecar-private-id",
        owner_scope=OwnerScope.APP,
        resource_kind="sidecar",
    )
    connection_record = registry.register_handle(
        resource_id="connection-private-id",
        owner_scope=OwnerScope.RUNTIME_CONNECTION,
        resource_kind="codex_app_server",
        runtime="codex",
        runtime_connection_id="codex-generation-1",
        runtime_generation=1,
    )

    assert app_record.agent_session_id is None
    assert connection_record.agent_session_id is None
    with pytest.raises(ValueError, match="agent_session_id"):
        registry.register_handle(
            resource_id="missing-session",
            owner_scope=OwnerScope.SESSION,
            resource_kind="claude_code",
        )
    with pytest.raises(ValueError, match="turn_id"):
        registry.register_handle(
            resource_id="missing-turn",
            owner_scope=OwnerScope.TURN,
            resource_kind="command",
            agent_session_id="session-private-id",
        )


def test_snapshot_redacts_owner_and_resource_ids(tmp_path: Path) -> None:
    registry = make_registry(tmp_path)
    registry.register_process_group(
        resource_id="cc-resource-private-id",
        owner_scope=OwnerScope.SESSION,
        resource_kind="claude_code_process_group",
        pid=410,
        agent_session_id="session-private-id",
        runtime="claude_code",
        runtime_generation=2,
    )

    payload = json.loads((tmp_path / "resource-lifecycle.json").read_text())
    encoded = json.dumps(payload)

    assert payload["version"] == 2
    assert payload["data_root_identity"] == redact_identity(str(tmp_path.absolute()))
    assert payload["app_instance_id"] != "app-private-id"
    assert "session-private-id" not in encoded
    assert "cc-resource-private-id" not in encoded
    assert payload["resources"][0]["pid"] == 410
    assert payload["resources"][0]["process_group"] == 410
    assert payload["resources"][0]["process_start_identity"] == "start-410"


def test_closed_requires_the_owner_to_have_no_live_resources(tmp_path: Path) -> None:
    registry = make_registry(tmp_path)
    record = registry.register_handle(
        resource_id="watcher-private-id",
        owner_scope=OwnerScope.SESSION,
        resource_kind="workflow_watcher",
        agent_session_id="session-private-id",
        runtime="claude_code",
    )

    registry.mark_owner_closing(
        OwnerScope.SESSION,
        agent_session_id="session-private-id",
    )
    assert registry.get(record.resource_id).state is ResourceState.CLOSING
    assert (
        registry.owner_summary(
            OwnerScope.SESSION,
            agent_session_id="session-private-id",
        ).live_resource_count
        == 1
    )

    registry.mark_closed(record.resource_id)

    summary = registry.owner_summary(
        OwnerScope.SESSION,
        agent_session_id="session-private-id",
    )
    assert summary.status == "closed"
    assert summary.live_resource_count == 0
    assert summary.remaining_resource_kinds == ()


def test_closed_resources_leave_only_a_bounded_recent_lookup_cache(
    tmp_path: Path,
) -> None:
    """安全快照和活资源表不能把已关闭历史永久累积下来。"""

    registry = make_registry(tmp_path)
    for index in range(300):
        resource_id = f"watcher-{index}"
        registry.register_handle(
            resource_id=resource_id,
            owner_scope=OwnerScope.SESSION,
            resource_kind="workflow_watcher",
            agent_session_id=f"session-{index}",
        )
        registry.mark_closed(resource_id)

    payload = json.loads((tmp_path / "resource-lifecycle.json").read_text())

    assert payload["resources"] == []
    assert len(registry._records) == 0
    assert len(registry._recent_closed) == 256
    assert registry.get("watcher-299").state is ResourceState.CLOSED
    with pytest.raises(KeyError):
        registry.get("watcher-0")


def test_owner_state_changes_publish_once_and_repeated_close_is_a_noop(
    tmp_path: Path,
) -> None:
    """同一 owner 的批量转换只落一次快照，重复终态不再 fsync。"""

    writes: list[dict[str, object]] = []
    registry = ResourceRegistry(
        app_instance_id="app-private-id",
        snapshot_path=tmp_path / "resource-lifecycle.json",
        process_controller=FakeProcessController(),
        now=lambda: datetime.fromisoformat("2026-08-01T12:00:00"),
        snapshot_writer=lambda _path, payload: writes.append(payload),
    )
    for index in range(8):
        registry.register_handle(
            resource_id=f"watcher-{index}",
            owner_scope=OwnerScope.SESSION,
            resource_kind="workflow_watcher",
            agent_session_id="session-private-id",
        )
    writes.clear()

    registry.mark_owner_closing(
        OwnerScope.SESSION,
        agent_session_id="session-private-id",
    )
    registry.mark_owner_closed(
        OwnerScope.SESSION,
        agent_session_id="session-private-id",
    )
    registry.mark_owner_closed(
        OwnerScope.SESSION,
        agent_session_id="session-private-id",
    )

    assert len(writes) == 2
    assert writes[-1]["resources"] == []
    with pytest.raises(RuntimeError, match="owner is closing"):
        registry.register_handle(
            resource_id="late-watcher",
            owner_scope=OwnerScope.SESSION,
            resource_kind="workflow_watcher",
            agent_session_id="session-private-id",
        )


def test_closed_owner_barriers_reject_late_callbacks_with_bounded_memory(
    tmp_path: Path,
) -> None:
    """近期 owner 终态继续挡住迟到登记，同时身份集合保持固定上限。"""

    registry = make_registry(tmp_path)
    for index in range(300):
        session_id = f"session-{index}"
        registry.mark_owner_closing(
            OwnerScope.SESSION,
            agent_session_id=session_id,
        )
        registry.mark_owner_closed(
            OwnerScope.SESSION,
            agent_session_id=session_id,
        )

    assert len(registry._closing_owners) == 0
    assert len(registry._owner_closing_started_at) == 0
    assert len(registry._recent_closed_owners) == 256
    with pytest.raises(RuntimeError, match="owner is closing"):
        registry.register_handle(
            resource_id="late-watcher",
            owner_scope=OwnerScope.SESSION,
            resource_kind="workflow_watcher",
            agent_session_id="session-299",
        )


def test_owner_close_observer_receives_no_owner_identity(tmp_path: Path) -> None:
    """owner 关闭回调只包含层级、耗时和数量，不携带内部身份。"""

    observations = []
    registry = ResourceRegistry(
        app_instance_id="app-private-id",
        snapshot_path=tmp_path / "resource-lifecycle.json",
        process_controller=FakeProcessController(),
        now=lambda: datetime.fromisoformat("2026-08-01T12:00:00+08:00"),
        owner_close_observer=observations.append,
    )
    registry.register_handle(
        resource_id="private-resource-id",
        owner_scope=OwnerScope.SESSION,
        resource_kind="workflow_watcher",
        agent_session_id="private-session-id",
    )

    registry.mark_owner_closing(
        OwnerScope.SESSION,
        agent_session_id="private-session-id",
    )
    registry.mark_owner_closed(
        OwnerScope.SESSION,
        agent_session_id="private-session-id",
    )
    registry.mark_owner_closed(
        OwnerScope.SESSION,
        agent_session_id="private-session-id",
    )

    assert len(observations) == 1
    assert observations[0].owner_scope is OwnerScope.SESSION
    assert observations[0].status == "closed"
    assert observations[0].closed_resource_count == 1
    assert "private" not in repr(observations[0])


def test_owner_needs_reconcile_observer_reports_remaining_without_identity(
    tmp_path: Path,
) -> None:
    """owner 未归零时保留屏障，并只上报层级、错误终态和残留数量。"""

    observations = []
    registry = ResourceRegistry(
        app_instance_id="app-private-id",
        snapshot_path=tmp_path / "resource-lifecycle.json",
        process_controller=FakeProcessController(),
        now=lambda: datetime.fromisoformat("2026-08-01T12:00:00+08:00"),
        owner_close_observer=observations.append,
    )
    registry.register_handle(
        resource_id="private-resource-id",
        owner_scope=OwnerScope.SESSION,
        resource_kind="workflow_watcher",
        agent_session_id="private-session-id",
    )
    registry.mark_owner_closing(
        OwnerScope.SESSION,
        agent_session_id="private-session-id",
    )

    registry.mark_owner_needs_reconcile(
        OwnerScope.SESSION,
        agent_session_id="private-session-id",
    )

    assert registry.get("private-resource-id").state is ResourceState.NEEDS_RECONCILE
    assert len(observations) == 1
    assert observations[0].owner_scope is OwnerScope.SESSION
    assert observations[0].status == "needs_reconcile"
    assert observations[0].remaining_resource_count == 1
    assert "private" not in repr(observations[0])
    with pytest.raises(RuntimeError, match="owner is closing"):
        registry.register_handle(
            resource_id="late-resource-id",
            owner_scope=OwnerScope.SESSION,
            resource_kind="workflow_watcher",
            agent_session_id="private-session-id",
        )


def test_closing_session_rejects_new_session_and_turn_resources(tmp_path: Path) -> None:
    """owner 开始关闭后不能再出现同会话的新临时资源。"""

    registry = make_registry(tmp_path)
    registry.mark_owner_closing(
        OwnerScope.SESSION,
        agent_session_id="session-private-id",
    )

    with pytest.raises(RuntimeError, match="owner is closing"):
        registry.register_handle(
            resource_id="late-watcher",
            owner_scope=OwnerScope.SESSION,
            resource_kind="workflow_watcher",
            agent_session_id="session-private-id",
        )
    with pytest.raises(RuntimeError, match="owner is closing"):
        registry.register_handle(
            resource_id="late-turn",
            owner_scope=OwnerScope.TURN,
            resource_kind="command",
            agent_session_id="session-private-id",
            turn_id="turn-private-id",
        )


def test_runtime_connection_close_marks_its_session_resources_closed(
    tmp_path: Path,
) -> None:
    """共享连接退出后，该连接代际中的 session handle 也应归零。"""

    registry = make_registry(tmp_path)
    registry.register_handle(
        resource_id="thread-private-id",
        owner_scope=OwnerScope.SESSION,
        resource_kind="codex_thread_resources",
        runtime="codex",
        runtime_generation=3,
        runtime_connection_id="codex-generation-3",
        agent_session_id="session-private-id",
    )

    registry.mark_owner_closed(
        OwnerScope.RUNTIME_CONNECTION,
        runtime_connection_id="codex-generation-3",
    )

    assert registry.get("thread-private-id").state is ResourceState.CLOSED


def test_app_reconcile_terminates_registered_process_group(tmp_path: Path) -> None:
    """应用 drain 会按登记身份终止并核验仍活着的独立进程组。"""

    controller = FakeProcessController()

    def signal_group(process_group: int, signal_name: str) -> None:
        assert (process_group, signal_name) == (410, "SIGTERM")
        controller.identities.clear()

    controller.signal_group = signal_group  # type: ignore[method-assign]
    registry = ResourceRegistry(
        app_instance_id="app-private-id",
        process_controller=controller,
    )
    registry.register_process_group(
        resource_id="worker-private-id",
        owner_scope=OwnerScope.APP,
        resource_kind="memory_review_process_group",
        pid=410,
    )

    report = registry.reconcile_process_groups(
        term_wait_seconds=0,
        kill_wait_seconds=0,
        poll_interval_seconds=0,
    )

    assert report.terminated == 1
    assert registry.get("worker-private-id").state is ResourceState.CLOSED


def test_reconcile_closes_duplicate_records_when_one_group_root_has_exited() -> None:
    """同组另一条记录仍可核验时，先退出的根 PID 不应制造假残留。"""

    controller = FakeProcessController()
    controller.identities = {
        410: ProcessIdentity(410, 500, "start-410"),
        411: ProcessIdentity(411, 500, "start-411"),
    }

    def signal_group(process_group: int, signal_name: str) -> None:
        assert (process_group, signal_name) == (500, "SIGTERM")
        controller.identities.clear()

    controller.signal_group = signal_group  # type: ignore[method-assign]
    registry = ResourceRegistry(
        app_instance_id="app-private-id",
        process_controller=controller,
    )
    for resource_id, pid in (("session-mcp", 410), ("connection-scan", 411)):
        registry.register_process_group(
            resource_id=resource_id,
            owner_scope=OwnerScope.APP,
            resource_kind="codex_descendant_process_group",
            pid=pid,
        )
    controller.identities.pop(410)

    report = registry.reconcile_process_groups(
        term_wait_seconds=0,
        kill_wait_seconds=0,
        poll_interval_seconds=0,
    )

    assert report.remaining == 0
    assert report.identity_mismatch == 0
    assert report.terminated == 1
    assert registry.get("session-mcp").state is ResourceState.CLOSED
    assert registry.get("connection-scan").state is ResourceState.CLOSED


def test_reported_process_uses_scoped_grant_without_leaking_token(
    tmp_path: Path,
) -> None:
    """间接启动的 MCP 只能用已绑定 owner 的令牌登记自身进程组。"""

    controller = FakeProcessController()
    controller.identities[400] = ProcessIdentity(
        pid=400,
        process_group=400,
        start_identity="start-400",
    )
    registry = ResourceRegistry(
        app_instance_id="app-private-id",
        snapshot_path=tmp_path / "resource-lifecycle.json",
        process_controller=controller,
        descendant_inventory=lambda root_pid: (
            (controller.identities[410],) if root_pid == 400 else ()
        ),
        registration_url="http://127.0.0.1:43183/api/desktop/resources/register",
        registration_credential="desktop-private-credential",
    )
    registry.register_process_group(
        resource_id="codex-app-server:3",
        owner_scope=OwnerScope.RUNTIME_CONNECTION,
        resource_kind="codex_app_server_process_group",
        pid=400,
        runtime="codex",
        runtime_generation=3,
        runtime_connection_id="codex-connection:3",
    )
    launch_env = registry.issue_process_registration(
        owner_scope=OwnerScope.SESSION,
        resource_kind="codex_memory_mcp_process_group",
        ancestor_resource_kind="codex_app_server_process_group",
        runtime="codex",
        agent_session_id="session-private-id",
    )

    record = registry.register_reported_process(
        launch_env["TROWEL_RESOURCE_REGISTRATION_TOKEN"],
        pid=410,
    )
    snapshot_text = (tmp_path / "resource-lifecycle.json").read_text()

    assert record.owner_scope is OwnerScope.SESSION
    assert record.agent_session_id == "session-private-id"
    assert record.runtime_generation == 3
    assert record.runtime_connection_id == "codex-connection:3"
    assert record.parent_resource_id == "codex-app-server:3"
    assert record.pid == 410
    assert launch_env["TROWEL_RESOURCE_REGISTRATION_URL"].endswith(
        "/api/desktop/resources/register"
    )
    assert (
        launch_env["TROWEL_RESOURCE_REGISTRATION_CREDENTIAL"]
        == "desktop-private-credential"
    )
    assert launch_env["TROWEL_RESOURCE_REGISTRATION_TOKEN"] not in snapshot_text
    assert "desktop-private-credential" not in snapshot_text
    with pytest.raises(ValueError, match="registration token"):
        registry.register_reported_process("unknown-token", pid=410)


def test_reported_process_rejects_pid_outside_runtime_process_tree(
    tmp_path: Path,
) -> None:
    """持有登记令牌的进程也不能把无关 PID 塞进 Trowel 账本。"""

    controller = FakeProcessController()
    controller.identities.update(
        {
            400: ProcessIdentity(400, 400, "start-400"),
            420: ProcessIdentity(420, 420, "start-420"),
        }
    )
    registry = ResourceRegistry(
        app_instance_id="app-private-id",
        process_controller=controller,
        descendant_inventory=lambda root_pid: (
            (controller.identities[410],) if root_pid == 400 else ()
        ),
        registration_url="http://127.0.0.1:43183/api/desktop/resources/register",
        registration_credential="desktop-private-credential",
    )
    registry.register_process_group(
        resource_id="codex-app-server:3",
        owner_scope=OwnerScope.RUNTIME_CONNECTION,
        resource_kind="codex_app_server_process_group",
        pid=400,
        runtime="codex",
        runtime_generation=3,
        runtime_connection_id="codex-connection:3",
    )
    launch_env = registry.issue_process_registration(
        owner_scope=OwnerScope.SESSION,
        resource_kind="codex_agent_mcp_process_group",
        ancestor_resource_kind="codex_app_server_process_group",
        runtime="codex",
        agent_session_id="session-private-id",
    )

    with pytest.raises(ValueError, match="runtime process tree"):
        registry.register_reported_process(
            launch_env["TROWEL_RESOURCE_REGISTRATION_TOKEN"],
            pid=420,
        )


@pytest.mark.skipif(os.name != "posix", reason="真实进程组核验只适用于 POSIX")
def test_reported_process_uses_real_ppid_tree_and_rejects_unrelated_pid() -> None:
    """生产进程表应接受真实后代，并拒绝同时存活的无关独立进程。"""

    parent_script = """
import subprocess
import sys
import time

child = subprocess.Popen(
    [sys.executable, "-c", "import time; time.sleep(30)"],
    start_new_session=True,
)
print(child.pid, flush=True)
time.sleep(30)
"""
    parent = subprocess.Popen(
        [sys.executable, "-c", parent_script],
        stdout=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    unrelated = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        start_new_session=True,
    )
    child_pid = -1
    try:
        assert parent.stdout is not None
        child_pid = int(parent.stdout.readline().strip())
        deadline = time.monotonic() + 2.0
        while child_pid not in {
            item.pid for item in list_descendant_processes(parent.pid)
        }:
            if time.monotonic() >= deadline:
                pytest.fail("真实子进程未进入父进程的 PPID 后代清单")
            time.sleep(0.02)

        registry = ResourceRegistry(
            app_instance_id="app-private-id",
            registration_url="http://127.0.0.1:43183/api/desktop/resources/register",
            registration_credential="desktop-private-credential",
        )
        registry.register_process_group(
            resource_id="codex-app-server:7",
            owner_scope=OwnerScope.RUNTIME_CONNECTION,
            resource_kind="codex_app_server_process_group",
            pid=parent.pid,
            runtime="codex",
            runtime_generation=7,
            runtime_connection_id="codex-connection:7",
        )
        launch_env = registry.issue_process_registration(
            owner_scope=OwnerScope.SESSION,
            resource_kind="codex_agent_mcp_process_group",
            ancestor_resource_kind="codex_app_server_process_group",
            runtime="codex",
            agent_session_id="session-private-id",
        )
        token = launch_env["TROWEL_RESOURCE_REGISTRATION_TOKEN"]

        accepted = registry.register_reported_process(token, pid=child_pid)
        assert accepted.runtime_connection_id == "codex-connection:7"
        with pytest.raises(ValueError, match="runtime process tree"):
            registry.register_reported_process(token, pid=unrelated.pid)
    finally:
        for pid in (child_pid, parent.pid, unrelated.pid):
            if pid <= 1:
                continue
            try:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
            except ProcessLookupError:
                pass
        parent.wait(timeout=2)
        unrelated.wait(timeout=2)
