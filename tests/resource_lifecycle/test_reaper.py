"""验证旧桌面实例残留进程只按启动身份收敛。"""

from __future__ import annotations

import json
from pathlib import Path

from trowel_py.resource_lifecycle import (
    ProcessIdentity,
    reconcile_previous_snapshot,
)
from trowel_py.resource_lifecycle.registry import redact_identity


class FakeProcessController:
    """记录 reaper 发出的信号，并模拟 PID 复用和进程退出。"""

    def __init__(self) -> None:
        self.identities = {
            501: ProcessIdentity(501, 501, "same-start"),
            502: ProcessIdentity(502, 502, "new-process-reused-pid"),
        }
        self.alive_groups = {501, 502}
        self.signals: list[tuple[int, str]] = []

    def inspect(self, pid: int) -> ProcessIdentity | None:
        """返回当前 PID 对应的测试身份。"""

        return self.identities.get(pid)

    def group_alive(self, process_group: int) -> bool:
        """判断测试进程组是否仍存活。"""

        return process_group in self.alive_groups

    def signal_group(self, process_group: int, signal_name: str) -> None:
        """记录信号；TERM 让匹配的测试进程组立即退出。"""

        self.signals.append((process_group, signal_name))
        if signal_name == "SIGTERM":
            self.alive_groups.discard(process_group)
            self.identities = {
                pid: identity
                for pid, identity in self.identities.items()
                if identity.process_group != process_group
            }


def write_snapshot(path: Path, *, instance_id: str = "old-instance") -> None:
    """写入一份只含进程身份的最小旧实例快照。"""

    path.write_text(
        json.dumps(
            {
                "version": 1,
                "app_instance_id": redact_identity(instance_id),
                "updated_at": "2026-08-01T11:59:00",
                "resources": [
                    {
                        "resource_id": "opaque-1",
                        "owner_scope": "session",
                        "resource_kind": "claude_code_process_group",
                        "pid": 501,
                        "process_group": 501,
                        "process_start_identity": "same-start",
                        "state": "running",
                    },
                    {
                        "resource_id": "opaque-2",
                        "owner_scope": "session",
                        "resource_kind": "claude_code_process_group",
                        "pid": 502,
                        "process_group": 502,
                        "process_start_identity": "old-process-before-reuse",
                        "state": "running",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )


def test_reaper_signals_only_matching_old_process_identity(tmp_path: Path) -> None:
    snapshot = tmp_path / "resource-lifecycle.json"
    write_snapshot(snapshot)
    controller = FakeProcessController()

    report = reconcile_previous_snapshot(
        snapshot,
        current_instance_id="new-instance",
        process_controller=controller,
        term_wait_seconds=0,
        poll_interval_seconds=0,
    )

    assert controller.signals == [(501, "SIGTERM")]
    assert report.terminated == 1
    assert report.identity_mismatch == 1
    assert report.remaining == 0


def test_reaper_never_touches_the_current_instance(tmp_path: Path) -> None:
    snapshot = tmp_path / "resource-lifecycle.json"
    write_snapshot(snapshot, instance_id="same-instance")
    controller = FakeProcessController()

    report = reconcile_previous_snapshot(
        snapshot,
        current_instance_id="same-instance",
        process_controller=controller,
    )

    assert controller.signals == []
    assert report.skipped_current_instance is True


def test_reaper_repeated_run_does_not_signal_terminated_group_again(
    tmp_path: Path,
) -> None:
    """同一旧快照重复处理时，已退出进程组保持幂等。"""

    snapshot = tmp_path / "resource-lifecycle.json"
    write_snapshot(snapshot)
    controller = FakeProcessController()

    first = reconcile_previous_snapshot(
        snapshot,
        current_instance_id="new-instance",
        process_controller=controller,
        term_wait_seconds=0,
        poll_interval_seconds=0,
    )
    second = reconcile_previous_snapshot(
        snapshot,
        current_instance_id="new-instance",
        process_controller=controller,
        term_wait_seconds=0,
        poll_interval_seconds=0,
    )

    assert controller.signals == [(501, "SIGTERM")]
    assert first.terminated == 1
    assert second.terminated == 0
    assert second.already_gone == 1


def test_reaper_reports_live_group_when_recorded_root_pid_is_gone(
    tmp_path: Path,
) -> None:
    """根 PID 消失但进程组仍活着时只能诊断，不能误报整个资源已退出。"""

    snapshot = tmp_path / "resource-lifecycle.json"
    write_snapshot(snapshot)
    payload = json.loads(snapshot.read_text(encoding="utf-8"))
    payload["resources"] = payload["resources"][:1]
    snapshot.write_text(json.dumps(payload), encoding="utf-8")
    controller = FakeProcessController()
    controller.identities.pop(501)
    controller.alive_groups = {501}

    report = reconcile_previous_snapshot(
        snapshot,
        current_instance_id="new-instance",
        process_controller=controller,
        term_wait_seconds=0,
        poll_interval_seconds=0,
    )

    assert controller.signals == []
    assert report.already_gone == 0
    assert report.identity_mismatch == 1
