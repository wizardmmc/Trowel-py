"""把一次研讨 attempt 的双 runtime 用量事件归一化为稳定摘要。"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Protocol

from trowel_py.agent_host.binding import Runtime

_CLAUDE_FIELDS = {
    "input_tokens": "input_tokens",
    "output_tokens": "output_tokens",
    "cache_read_input_tokens": "cache_read_input_tokens",
    "cache_creation_input_tokens": "cache_creation_input_tokens",
}
_CODEX_FIELDS = {
    "inputTokens": "input_tokens",
    "outputTokens": "output_tokens",
    "cachedInputTokens": "cache_read_input_tokens",
    "reasoningOutputTokens": "reasoning_output_tokens",
    "totalTokens": "total_tokens",
}


class _UsageAccumulator(Protocol):
    """定义单一 runtime 用量累积策略的最小接口。"""

    def observe(self, event_type: object, payload: Mapping[str, object]) -> None:
        """接收一条已核对为当前根 turn 的事件。"""

    def summary(self) -> dict[str, int] | None:
        """返回当前 attempt 的稳定 token 摘要。"""


class _ClaudeUsageAccumulator:
    """按 message ID 去重 Claude Code 的 assistant 用量快照。"""

    def __init__(self) -> None:
        """创建当前 attempt 独占的消息快照表。"""

        self._messages: dict[str, dict[str, int]] = {}

    def observe(self, event_type: object, payload: Mapping[str, object]) -> None:
        """保存 context_usage 中一条消息的最新完整快照。"""

        if event_type != "context_usage":
            return
        message_id = payload.get("message_id")
        usage = payload.get("usage")
        if not isinstance(message_id, str) or not message_id:
            return
        normalized = _normalize_usage(usage, _CLAUDE_FIELDS)
        if not normalized:
            return
        if all(field in normalized for field in _CLAUDE_FIELDS.values()):
            normalized["total_tokens"] = sum(normalized.values())
        self._messages[message_id] = normalized

    def summary(self) -> dict[str, int] | None:
        """合并去重后的消息用量，不把缺失分类臆测成零。"""

        return _sum_usage(self._messages.values())


class _CodexUsageAccumulator:
    """把 Codex 会话累计水位换算为当前 attempt 的增量。"""

    def __init__(self) -> None:
        """创建当前 attempt 独占的上一水位和分类增量。"""

        self._previous_total: dict[str, int] | None = None
        self._deltas: list[dict[str, int]] = []

    def observe(self, event_type: object, payload: Mapping[str, object]) -> None:
        """按相邻 total 水位求差，首条事件以 last 作为本轮首个增量。"""

        if event_type != "usage_updated":
            return
        total = _normalize_usage(payload.get("total"), _CODEX_FIELDS)
        if not total:
            return
        if self._previous_total is None:
            delta = _normalize_usage(payload.get("last"), _CODEX_FIELDS)
        else:
            delta = _subtract_usage(total, self._previous_total)
        self._previous_total = total
        if delta:
            self._deltas.append(delta)

    def summary(self) -> dict[str, int] | None:
        """合并当前 attempt 内所有模型调用的水位增量。"""

        return _sum_usage(self._deltas)


class AttemptUsageAccumulator:
    """向协调器隐藏 Claude Code 与 Codex 的不同用量协议。"""

    def __init__(self, runtime: Runtime) -> None:
        """为 participant 冻结的 runtime 选择对应累积策略。

        Args:
            runtime: 当前研讨参与者的冻结运行工具。
        """

        strategies: dict[Runtime, _UsageAccumulator] = {
            Runtime.CLAUDE_CODE: _ClaudeUsageAccumulator(),
            Runtime.CODEX: _CodexUsageAccumulator(),
        }
        self._strategy = strategies[runtime]

    def observe(self, event_type: object, payload: object) -> None:
        """接收一条当前根 turn 事件；非用量或无效负载会被忽略。

        Args:
            event_type: 统一 AgentEvent 类型。
            payload: 统一 AgentEvent 的原始 payload。
        """

        if isinstance(payload, Mapping):
            self._strategy.observe(event_type, payload)

    def summary(self) -> dict[str, int] | None:
        """返回供持久化和界面汇总使用的统一 snake_case 摘要。"""

        return self._strategy.summary()


def _normalize_usage(
    raw: object,
    fields: Mapping[str, str],
) -> dict[str, int]:
    """从原生对象提取非负整数 token 字段。

    Args:
        raw: runtime 回报的单份 usage 对象。
        fields: 原生字段到统一字段的映射。

    Returns:
        只含已知非负整数的统一分类。
    """

    if not isinstance(raw, Mapping):
        return {}
    normalized: dict[str, int] = {}
    for source, target in fields.items():
        value = raw.get(source)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            normalized[target] = value
    return normalized


def _subtract_usage(end: Mapping[str, int], start: Mapping[str, int]) -> dict[str, int]:
    """计算两个累计水位的逐分类非负差。

    Args:
        end: 新累计水位。
        start: 上一次累计水位。

    Returns:
        两端都有且没有倒退的分类增量。
    """

    return {
        field: value - start[field]
        for field, value in end.items()
        if field in start and value >= start[field]
    }


def _sum_usage(values: Iterable[Mapping[str, int]]) -> dict[str, int] | None:
    """按分类合并若干已归一化样本；完全无样本时返回 None。

    Args:
        values: 同一 attempt 内已归一化且去重或求差后的样本。

    Returns:
        各已知分类的小计；没有任何可用 token 时为 None。
    """

    totals: dict[str, int] = {}
    for usage in values:
        for field, value in usage.items():
            totals[field] = totals.get(field, 0) + value
    return totals or None
