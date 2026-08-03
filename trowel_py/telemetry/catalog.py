"""集中保存遥测稳定枚举和动态维度的大小边界。"""

from __future__ import annotations

COMPONENTS = frozenset(
    {
        "electron",
        "renderer",
        "fastapi",
        "agent_host",
        "runtime",
        "mcp",
        "sqlite",
        "telemetry",
    }
)

OPERATIONS = frozenset(
    {
        "desktop.start",
        "desktop.exit",
        "renderer.measure",
        "http.agent.messages",
        "http.statistics.query",
        "agent.turn",
        "agent.interrupt",
        "runtime.call",
        "runtime.tool",
        "mcp.tools.call",
        "sqlite.query",
        "sqlite.transaction",
        "telemetry.collect",
        "telemetry.flush",
        "telemetry.aggregate",
        "telemetry.cleanup",
    }
)

STATUSES = frozenset({"ok", "error", "unset"})
RUNTIMES = frozenset({"claude_code", "codex"})

# 模型名来自 runtime 的实际回报，不能用会随供应商演进的型号名单限制。
MODEL_NAME_MAX_LENGTH = 256

METRIC_NAMES = frozenset(
    {
        "telemetry.accepted",
        "telemetry.rejected",
        "telemetry.dropped",
        "telemetry.database_bytes",
    }
)

METRIC_KINDS = frozenset({"counter", "gauge", "histogram"})
METRIC_UNITS = frozenset({"1", "By", "ms"})

# 非累计 bucket；小时和日聚合可以逐 bucket 相加，不会平均分位数。
DURATION_HISTOGRAM_UPPER_BOUNDS_MS: tuple[float | None, ...] = (
    1.0,
    5.0,
    10.0,
    25.0,
    50.0,
    100.0,
    250.0,
    500.0,
    1_000.0,
    2_500.0,
    5_000.0,
    10_000.0,
    30_000.0,
    60_000.0,
    None,
)
