"""读取上一桌面实例快照，并收敛身份仍匹配的独立进程组。"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import cast

from trowel_py.resource_lifecycle.models import ReconcileReport
from trowel_py.resource_lifecycle.processes import (
    LocalProcessController,
    ProcessController,
)
from trowel_py.resource_lifecycle.registry import SNAPSHOT_VERSION, redact_identity


def reconcile_previous_snapshot(
    snapshot_path: Path,
    *,
    current_instance_id: str,
    process_controller: ProcessController | None = None,
    term_wait_seconds: float = 3.0,
    kill_wait_seconds: float = 1.0,
    poll_interval_seconds: float = 0.05,
) -> ReconcileReport:
    """终止旧实例中 PID、进程组和启动指纹仍完全匹配的资源。

    Args:
        snapshot_path: 上一 sidecar 原子发布的资源快照。
        current_instance_id: 本次 Host 生成的原始应用实例 ID。
        process_controller: 进程身份检查和信号实现；省略时使用本机实现。
        term_wait_seconds: SIGTERM 后等待进程组退出的最长秒数。
        kill_wait_seconds: SIGKILL 后等待进程组退出的最长秒数。
        poll_interval_seconds: 两次进程组存活检查之间的秒数。

    Returns:
        已退出、身份不匹配、仍存活和错误数量组成的处理报告。
    """

    payload = _read_snapshot(snapshot_path)
    if payload is None:
        return ReconcileReport()
    if payload.get("app_instance_id") == redact_identity(current_instance_id):
        return ReconcileReport(skipped_current_instance=True)
    controller = process_controller or LocalProcessController()
    already_gone = 0
    identity_mismatch = 0
    errors: list[str] = []
    matched_groups: set[int] = set()
    resources = cast(list[object], payload.get("resources", []))
    for item in resources:
        if not isinstance(item, dict) or item.get("state") == "closed":
            continue
        pid = item.get("pid")
        process_group = item.get("process_group")
        start_identity = item.get("process_start_identity")
        if not isinstance(pid, int) or not isinstance(process_group, int):
            continue
        if not isinstance(start_identity, str) or not start_identity:
            continue
        current = controller.inspect(pid)
        if current is None:
            if controller.group_alive(process_group):
                identity_mismatch += 1
            else:
                already_gone += 1
            continue
        if (
            current.process_group != process_group
            or current.start_identity != start_identity
        ):
            identity_mismatch += 1
            continue
        matched_groups.add(process_group)

    signaled_groups: set[int] = set()
    for process_group in sorted(matched_groups):
        try:
            controller.signal_group(process_group, "SIGTERM")
            signaled_groups.add(process_group)
        except (ProcessLookupError, PermissionError, OSError, RuntimeError) as exc:
            errors.append(f"SIGTERM group {process_group}: {type(exc).__name__}")
    remaining = _wait_remaining(
        signaled_groups,
        controller,
        timeout=term_wait_seconds,
        poll_interval=poll_interval_seconds,
    )
    for process_group in sorted(remaining):
        try:
            controller.signal_group(process_group, "SIGKILL")
        except (ProcessLookupError, PermissionError, OSError, RuntimeError) as exc:
            errors.append(f"SIGKILL group {process_group}: {type(exc).__name__}")
    remaining = _wait_remaining(
        remaining,
        controller,
        timeout=kill_wait_seconds,
        poll_interval=poll_interval_seconds,
    )
    return ReconcileReport(
        terminated=len(signaled_groups - remaining),
        already_gone=already_gone,
        identity_mismatch=identity_mismatch,
        remaining=len(remaining),
        errors=tuple(errors),
    )


def _read_snapshot(path: Path) -> dict[str, object] | None:
    """读取受支持的快照；缺失、损坏或版本未知时返回 None。"""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("version") != SNAPSHOT_VERSION:
        return None
    if not isinstance(payload.get("resources"), list):
        return None
    return payload


def _wait_remaining(
    process_groups: set[int],
    controller: ProcessController,
    *,
    timeout: float,
    poll_interval: float,
) -> set[int]:
    """在有界等待后返回仍存活的进程组。

    Args:
        process_groups: 已发送过信号的进程组集合。
        controller: 判断进程组是否仍存活的实现。
        timeout: 最长等待秒数。
        poll_interval: 两次检查之间的秒数。
    """

    deadline = time.monotonic() + max(timeout, 0.0)
    remaining = {
        process_group
        for process_group in process_groups
        if controller.group_alive(process_group)
    }
    while remaining and time.monotonic() < deadline:
        if poll_interval > 0:
            time.sleep(min(poll_interval, max(deadline - time.monotonic(), 0.0)))
        remaining = {
            process_group
            for process_group in remaining
            if controller.group_alive(process_group)
        }
    return remaining
