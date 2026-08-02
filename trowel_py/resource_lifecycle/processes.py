"""读取进程启动身份，并只向已核验的独立进程组发送信号。"""

from __future__ import annotations

import hashlib
import os
import signal
import subprocess
from typing import Callable, Protocol

from trowel_py.resource_lifecycle.models import ProcessIdentity

DescendantInventory = Callable[[int], tuple[ProcessIdentity, ...]]


class ProcessController(Protocol):
    """约束 registry、Host 和 reaper 使用的进程身份操作。"""

    def inspect(self, pid: int) -> ProcessIdentity | None:
        """读取 PID 当前对应的进程组和启动指纹；进程不存在时返回 None。"""

        ...

    def group_alive(self, process_group: int) -> bool:
        """判断进程组中是否仍有可见进程。"""

        ...

    def signal_group(self, process_group: int, signal_name: str) -> None:
        """向独立进程组发送指定 POSIX 信号。"""

        ...


class LocalProcessController:
    """通过系统进程表和 POSIX 进程组管理本机 Trowel 子进程。"""

    def inspect(self, pid: int) -> ProcessIdentity | None:
        """读取进程组、启动时间和可执行文件，并生成启动指纹。

        Args:
            pid: 要核对的进程 ID。

        Returns:
            当前进程身份；进程不存在或系统无法读取时为 None。
        """

        if pid <= 0:
            return None
        result = subprocess.run(
            ["ps", "-o", "pgid=", "-o", "lstart=", "-o", "comm=", "-p", str(pid)],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None
        parts = result.stdout.strip().split(maxsplit=6)
        if len(parts) < 7:
            return None
        try:
            process_group = int(parts[0])
        except ValueError:
            return None
        stable_source = " ".join(parts[1:])
        start_identity = hashlib.sha256(stable_source.encode("utf-8")).hexdigest()
        return ProcessIdentity(
            pid=pid,
            process_group=process_group,
            start_identity=start_identity,
        )

    def group_alive(self, process_group: int) -> bool:
        """用信号 0 判断进程组是否仍存在。

        Args:
            process_group: 要检查的 POSIX 进程组 ID。
        """

        if process_group <= 1:
            return False
        try:
            os.killpg(process_group, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def signal_group(self, process_group: int, signal_name: str) -> None:
        """拒绝当前进程组后，向目标独立进程组发送信号。

        Args:
            process_group: 已由调用方核验身份的 POSIX 进程组 ID。
            signal_name: `SIGINT`、`SIGTERM` 或 `SIGKILL`。

        Raises:
            ValueError: 信号名称不在允许集合中。
            RuntimeError: 目标不是可单独终止的进程组，或等于当前进程组。
        """

        allowed = {
            "SIGINT": signal.SIGINT,
            "SIGTERM": signal.SIGTERM,
            "SIGKILL": signal.SIGKILL,
        }
        signum = allowed.get(signal_name)
        if signum is None:
            raise ValueError(f"unsupported process-group signal: {signal_name}")
        if process_group <= 1 or process_group == os.getpgrp():
            raise RuntimeError("refusing to signal a non-independent process group")
        os.killpg(process_group, signum)


def list_descendant_processes(root_pid: int) -> tuple[ProcessIdentity, ...]:
    """从单次系统进程表快照返回指定根进程的全部后代启动身份。

    Args:
        root_pid: 已由 Trowel 直接启动并登记身份的根进程 PID。

    Returns:
        仍能从 PPID 链追溯到根进程的后代，不包含根进程本身。
    """

    if root_pid <= 0:
        return ()
    result = subprocess.run(
        ["ps", "-axo", "pid=,ppid=,pgid=,lstart=,comm="],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return ()
    rows: dict[int, tuple[int, ProcessIdentity]] = {}
    for line in result.stdout.splitlines():
        parts = line.strip().split(maxsplit=8)
        if len(parts) < 9:
            continue
        try:
            pid = int(parts[0])
            parent_pid = int(parts[1])
            process_group = int(parts[2])
        except ValueError:
            continue
        stable_source = " ".join(parts[3:])
        rows[pid] = (
            parent_pid,
            ProcessIdentity(
                pid=pid,
                process_group=process_group,
                start_identity=hashlib.sha256(
                    stable_source.encode("utf-8")
                ).hexdigest(),
            ),
        )

    descendants: list[ProcessIdentity] = []
    for pid, (_, identity) in rows.items():
        ancestor = pid
        visited: set[int] = set()
        while ancestor in rows and ancestor not in visited:
            if ancestor == root_pid:
                if pid != root_pid:
                    descendants.append(identity)
                break
            visited.add(ancestor)
            ancestor = rows[ancestor][0]
    return tuple(descendants)
