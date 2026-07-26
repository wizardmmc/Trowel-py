"""只读系统时钟、进程和文件状态，不解释任务是否语义完成。"""

from __future__ import annotations

import ctypes
import hashlib
import platform
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Callable

from trowel_py.model_os.waking.models import (
    WakeCondition,
    WakeConditionKind,
    WakeObservation,
)


def _instant(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _identity(*parts: object) -> str:
    raw = "\0".join(str(part) for part in parts).encode()
    return hashlib.sha256(raw).hexdigest()


def read_process_start_identity(pid: int) -> str | None:
    """返回可防 PID reuse 的进程启动身份；平台不支持或进程不存在时返回 None。"""

    if pid <= 0:
        return None
    proc_stat = Path(f"/proc/{pid}/stat")
    if proc_stat.exists():
        try:
            raw = proc_stat.read_text(encoding="utf-8")
            fields = raw[raw.rfind(")") + 2 :].split()
            return f"linux:{fields[19]}"
        except (OSError, IndexError):
            return None
    if platform.system() == "Darwin":
        try:
            result = subprocess.run(
                ["ps", "-o", "lstart=", "-p", str(pid)],
                check=False,
                capture_output=True,
                text=True,
                timeout=2,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        started = result.stdout.strip()
        return f"darwin:{started}" if result.returncode == 0 and started else None
    return None


class SystemObserver:
    def observe(
        self, condition: WakeCondition, *, observed_at: str
    ) -> WakeObservation | None:
        if condition.kind is WakeConditionKind.TIME:
            if condition.due_at is None or _instant(observed_at) < _instant(
                condition.due_at
            ):
                return None
            return WakeObservation(
                observation_id=f"time:{condition.condition_id}:{condition.due_at}",
                kind=WakeConditionKind.TIME,
                target_ref="clock",
                observed_at=observed_at,
                source="timer",
                details={},
            )
        state_kind = condition.match_params.get("state_kind")
        if condition.kind is not WakeConditionKind.OBSERVED_STATE:
            return None
        if state_kind == "file":
            return self._file(condition, observed_at)
        if state_kind == "process":
            return self._process(condition, observed_at)
        return None

    @staticmethod
    def _file(
        condition: WakeCondition, observed_at: str
    ) -> WakeObservation | None:
        if not condition.target_ref.startswith("file:"):
            return None
        path = Path(condition.target_ref.removeprefix("file:"))
        try:
            stat = path.stat()
        except FileNotFoundError:
            details = {"state_kind": "file", "state": "missing"}
        except OSError:
            return None
        else:
            details = {
                "state_kind": "file",
                "state": "exists",
                "identity": f"{stat.st_dev}:{stat.st_ino}:{stat.st_mtime_ns}:{stat.st_size}",
            }
        return WakeObservation(
            observation_id=f"file:{condition.condition_id}:{_identity(details)}",
            kind=WakeConditionKind.OBSERVED_STATE,
            target_ref=condition.target_ref,
            observed_at=observed_at,
            source="file-observer",
            details=details,
        )

    @staticmethod
    def _process(
        condition: WakeCondition, observed_at: str
    ) -> WakeObservation | None:
        if not condition.target_ref.startswith("process:"):
            return None
        expected_identity = condition.match_params.get("start_identity")
        if not isinstance(expected_identity, str) or not expected_identity:
            return None
        try:
            pid = int(condition.target_ref.removeprefix("process:"))
        except ValueError:
            return None
        actual_identity = read_process_start_identity(pid)
        if actual_identity is None:
            details = {
                "state_kind": "process",
                "state": "exited",
                "start_identity": expected_identity,
            }
        else:
            details = {
                "state_kind": "process",
                "state": "running",
                "start_identity": actual_identity,
            }
        return WakeObservation(
            observation_id=f"process:{condition.condition_id}:{_identity(details)}",
            kind=WakeConditionKind.OBSERVED_STATE,
            target_ref=condition.target_ref,
            observed_at=observed_at,
            source="process-observer",
            details=details,
        )


@dataclass(frozen=True)
class HostClockSample:
    boot_identity: str
    active_ns: int
    continuous_ns: int


class HostSuspendDetector:
    """比较 suspend-aware 与 active-only 时钟；wall clock 调整不参与判断。"""

    def __init__(
        self,
        sample: Callable[[], HostClockSample],
        *,
        minimum_suspend_seconds: float = 1.0,
    ) -> None:
        self._sample = sample
        self._minimum_suspend_ns = int(minimum_suspend_seconds * 1_000_000_000)
        self._previous: HostClockSample | None = None

    def poll(self) -> str | None:
        current = self._sample()
        previous = self._previous
        self._previous = current
        if previous is None:
            return None
        if current.boot_identity != previous.boot_identity:
            return "boot_changed"
        active_delta = current.active_ns - previous.active_ns
        continuous_delta = current.continuous_ns - previous.continuous_ns
        if active_delta < 0 or continuous_delta < 0:
            return None
        suspended_ns = continuous_delta - active_delta
        return "wake" if suspended_ns >= self._minimum_suspend_ns else None


@lru_cache(maxsize=1)
def read_boot_identity() -> str:
    if platform.system() == "Linux":
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    if platform.system() == "Darwin":
        result = subprocess.run(
            ["sysctl", "-n", "kern.boottime"],
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
        return result.stdout.strip()
    raise RuntimeError("host boot identity is unavailable on this platform")


class _MachTimebase(ctypes.Structure):
    _fields_ = [("numer", ctypes.c_uint32), ("denom", ctypes.c_uint32)]


def _darwin_continuous_ns() -> int:
    library = ctypes.CDLL(None)
    continuous = library.mach_continuous_time
    continuous.restype = ctypes.c_uint64
    info = _MachTimebase()
    if library.mach_timebase_info(ctypes.byref(info)) != 0 or info.denom == 0:
        raise RuntimeError("mach timebase is unavailable")
    return continuous() * info.numer // info.denom


def default_host_clock_sample() -> HostClockSample:
    system = platform.system()
    if system == "Darwin" and hasattr(time, "CLOCK_UPTIME_RAW"):
        active_ns = time.clock_gettime_ns(time.CLOCK_UPTIME_RAW)
        continuous_ns = _darwin_continuous_ns()
    elif system == "Linux" and hasattr(time, "CLOCK_BOOTTIME"):
        active_ns = time.monotonic_ns()
        continuous_ns = time.clock_gettime_ns(time.CLOCK_BOOTTIME)
    else:
        raise RuntimeError("suspend-aware host clocks are unavailable")
    return HostClockSample(read_boot_identity(), active_ns, continuous_ns)
