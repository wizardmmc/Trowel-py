"""检测 CC stdout 静默时长，不直接操作进程。

上游 stream-json 可能死锁，但长时间静默也可能是合法等待。检测器只返回阶段；
service 在 mild 和 severe 阶段提示，并在可配置的 kill 阈值结束轮次。所有时间值
由调用方使用同一时钟提供，已知的 `api_retry` backoff 期间保持 quiet。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class StalledDetector:
    """根据 stdout 静默时间和 `api_retry` backoff 计算停滞阶段。

    本对象只保存计时状态并返回阶段，不发布事件或操作进程。

    Attributes:
        threshold_mild: 进入 `mild` 阶段所需的静默秒数，默认为 120。
        threshold_severe: 进入 `severe` 阶段所需的静默秒数，默认为 300。
        threshold_kill: 进入 `kill` 阶段所需的静默秒数，默认为 1800。
        _last_event_at: 最近一次开始轮次或记录活动的时钟值；尚未开始时为 `None`。
        _retry_until: 当前 `api_retry` 豁免截止的时钟值；没有豁免时为 `None`。
    """

    threshold_mild: float = 120.0
    threshold_severe: float = 300.0
    threshold_kill: float = 1800.0
    _last_event_at: float | None = None
    _retry_until: float | None = None

    def start_turn(self, now: float) -> None:
        """从当前时刻开始计算新轮次的静默时间，并清除重试豁免。

        Args:
            now: 新轮次开始时的时钟值，单位为秒。
        """

        self._last_event_at = now
        self._retry_until = None

    def record_event(self, now: float) -> None:
        """记录最新活动时间，并清除已经到期的重试豁免。

        尚未到期的豁免继续生效。

        Args:
            now: 收到 stdout 或其他有效活动时的时钟值，单位为秒。
        """

        self._last_event_at = now
        if self._retry_until is not None and now >= self._retry_until:
            self._retry_until = None

    def record_retry(self, now: float, retry_delay_ms: float) -> None:
        """将 CC `api_retry` 延迟从毫秒换算为秒，并覆盖 quiet 豁免截止时间。

        新截止时间为 ``now + retry_delay_ms / 1000``。

        Args:
            now: 收到 `api_retry` 事件时的时钟值，单位为秒。
            retry_delay_ms: CC 报告的重试等待时长，单位为毫秒。
        """
        self._retry_until = now + retry_delay_ms / 1000.0

    def quiet_seconds(self, now: float) -> float:
        """返回距最近一次已记录活动经过的秒数。

        Args:
            now: 用于计算差值的当前时钟值，单位为秒。

        Returns:
            静默秒数；尚未开始轮次时返回 0。
        """

        if self._last_event_at is None:
            return 0.0
        return now - self._last_event_at

    def phase(self, now: float) -> str:
        """在重试豁免期返回 quiet，否则按静默阈值返回当前阶段。

        达到阈值即进入对应阶段，优先级为 `kill`、`severe`、`mild`、`quiet`。

        Args:
            now: 用于判断阶段的当前时钟值，单位为秒。

        Returns:
            `quiet`、`mild`、`severe` 或 `kill`；尚未开始轮次时为 `quiet`。
        """
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
