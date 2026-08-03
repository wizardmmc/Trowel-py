"""提供从 L01 production-shape 基准收敛出的遥测测试样本。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from trowel_py.telemetry.contracts import TelemetryBatchRequest


BASE_TIME = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)

COMPONENT_OPERATIONS: tuple[tuple[str, str], ...] = (
    ("electron", "desktop.start"),
    ("renderer", "renderer.measure"),
    ("fastapi", "http.agent.messages"),
    ("fastapi", "http.statistics.query"),
    ("agent_host", "agent.turn"),
    ("runtime", "runtime.call"),
    ("mcp", "mcp.tools.call"),
    ("sqlite", "sqlite.query"),
)


def span_payload(
    index: int = 1,
    *,
    component: str = "fastapi",
    operation: str = "http.statistics.query",
    started_at: datetime | None = None,
    duration_ms: float = 12.5,
) -> dict[str, object]:
    """生成一条只含 L01 白名单字段的确定性 span。"""

    start = started_at or BASE_TIME
    end = start + timedelta(milliseconds=duration_ms)
    return {
        "trace_id": f"{index:032x}",
        "span_id": f"{index:016x}",
        "started_at": start.isoformat(),
        "ended_at": end.isoformat(),
        "component": component,
        "operation": operation,
        "status": "ok",
        "runtime": None,
        "model": None,
        "session_ref": f"session-{index % 1000}",
        "call_ref": None,
        "attributes": {
            "quality": "reliable",
            "sampled": True,
            "retry_count": 0,
        },
        "links": [],
    }


def metric_payload(
    index: int = 1,
    *,
    observed_at: datetime | None = None,
) -> dict[str, object]:
    """生成一条 collector 自观测 metric。"""

    return {
        "metric_id": f"metric-{index}",
        "observed_at": (observed_at or BASE_TIME).isoformat(),
        "component": "telemetry",
        "name": "telemetry.accepted",
        "kind": "counter",
        "unit": "1",
        "value": float(index),
        "status": "ok",
        "runtime": None,
        "model": None,
        "operation": "telemetry.collect",
        "attributes": {"quality": "reliable", "sampled": True},
    }


def batch_request(
    batch_id: str = "batch-0001",
    *,
    spans: list[dict[str, object]] | None = None,
    metrics: list[dict[str, object]] | None = None,
    mode: str = "normal",
) -> TelemetryBatchRequest:
    """构造一个版本化遥测批次。"""

    return TelemetryBatchRequest(
        batch_id=batch_id,
        schema_version=1,
        source_component="fastapi",
        collected_at=BASE_TIME,
        mode=mode,
        spans=spans if spans is not None else [span_payload()],
        metrics=metrics or [],
    )
