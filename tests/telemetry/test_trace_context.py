"""验证 W3C 父上下文、进程内子 span 和 runtime link。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient

from trowel_py.telemetry.events import (
    activate_trace_context,
    create_span_context,
    emit_span,
    format_traceparent,
    parse_traceparent,
)
from trowel_py.telemetry.http_middleware import RuntimeTelemetryMiddleware
from trowel_py.telemetry.sqlite import configure_sqlite_telemetry, open_observed_sqlite


class CapturingPort:
    """在内存中保存测试期间提交的 span。"""

    def __init__(self) -> None:
        self.spans = []

    def emit_span(self, span):
        self.spans.append(span)

    def emit_metric(self, _metric):
        return None


def test_traceparent_parser_and_child_span_preserve_real_parentage() -> None:
    parent = parse_traceparent(
        "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    )
    assert parent is not None
    server = create_span_context(parent=parent)
    port = CapturingPort()
    started = datetime.now(UTC)

    with activate_trace_context(server):
        emit_span(
            port,
            component="sqlite",
            operation="sqlite.query",
            started_at=started,
            ended_at=started + timedelta(milliseconds=1),
        )

    child = port.spans[0]
    assert child.trace_id == parent.trace_id
    assert child.parent_span_id == server.span_id
    assert format_traceparent(server).startswith(
        f"00-{parent.trace_id}-{server.span_id}-"
    )
    assert parse_traceparent("00-invalid") is None


def test_http_context_makes_observed_sqlite_a_real_child() -> None:
    port = CapturingPort()
    configure_sqlite_telemetry(port)
    app = FastAPI()
    app.state.telemetry_port = port
    app.add_middleware(RuntimeTelemetryMiddleware)

    @app.get("/api/statistics/probe")
    def probe():
        connection = open_observed_sqlite(":memory:", domain="sessions")
        try:
            connection.execute("SELECT 1").fetchone()
        finally:
            connection.close()
        return {"ok": True}

    try:
        response = TestClient(app).get(
            "/api/statistics/probe",
            headers={
                "traceparent": (
                    "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
                )
            },
        )
    finally:
        configure_sqlite_telemetry(None)

    assert response.status_code == 200
    http = next(span for span in port.spans if span.component == "fastapi")
    sqlite = next(span for span in port.spans if span.component == "sqlite")
    assert http.trace_id == "4bf92f3577b34da6a3ce929d0e0e4736"
    assert http.parent_span_id == "00f067aa0ba902b7"
    assert sqlite.trace_id == http.trace_id
    assert sqlite.parent_span_id == http.span_id
