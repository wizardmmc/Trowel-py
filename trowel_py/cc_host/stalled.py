"""检测 CC stdout 静默时长，不直接操作进程。

上游 stream-json 可能死锁，但 GLM 的长静默也可能是合法等待，因此 mild 与
severe 阶段只提示，service 仅在 30 分钟 hard cap 时终止进程。时钟由调用者注入，
已知 api_retry backoff 期间保持 quiet。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class StalledDetector:
    """根据静默时间与 retry backoff 计算阶段，不发事件也不 kill。"""

    threshold_mild: float = 120.0
    threshold_severe: float = 300.0
    threshold_kill: float = 1800.0
    _last_event_at: float | None = None
    _retry_until: float | None = None

    def start_turn(self, now: float) -> None:
        self._last_event_at = now
        self._retry_until = None

    def record_event(self, now: float) -> None:
        self._last_event_at = now
        if self._retry_until is not None and now >= self._retry_until:
            self._retry_until = None

    def record_retry(self, now: float, retry_delay_ms: float) -> None:
        """把 CC api_retry 延迟换算为 quiet 豁免窗口。"""
        self._retry_until = now + retry_delay_ms / 1000.0

    def quiet_seconds(self, now: float) -> float:
        if self._last_event_at is None:
            return 0.0
        return now - self._last_event_at

    def phase(self, now: float) -> str:
        """retry 窗口内保持 quiet，否则按三个阈值依次升级。"""
        if self._last_event_at is None:
            return "quiet"
        if self._retry_until is not None and now < self._retry_until:
            return "quiet"
        quiet = now - self._last_event_at
        if quiet >= self.threshold_kill:
            return "kill"
        if quiet >= self.threshold_severe:
            return "severe"
        if quiet >= self.threshold_mild:
            return "mild"
        return "quiet"
