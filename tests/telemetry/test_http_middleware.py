"""验证 FastAPI 运行埋点只使用路由模板分组且不影响响应。"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from trowel_py.telemetry.http_middleware import RuntimeTelemetryMiddleware


class RecordingPort:
    """记录测试中间件发出的 span。"""

    def __init__(self) -> None:
        """创建空 span 列表。"""

        self.spans = []

    def emit_span(self, span):
        """保存 span 并返回最小提交结果替身。"""

        self.spans.append(span)
        return None

    def emit_metric(self, _metric):
        """本测试不接收 metric。"""

        return None


def test_middleware_groups_dynamic_statistics_path_without_recording_id() -> None:
    """动态 URL 身份不会进入 operation、attributes 或引用字段。"""

    app = FastAPI()
    port = RecordingPort()
    app.state.telemetry_port = port
    app.add_middleware(RuntimeTelemetryMiddleware)

    @app.get("/api/statistics/items/{item_id}")
    def item(item_id: str) -> dict[str, str]:
        """返回测试动态参数。"""

        return {"item": item_id}

    response = TestClient(app).get("/api/statistics/items/private-session-42")

    assert response.status_code == 200
    assert response.json() == {"item": "private-session-42"}
    assert len(port.spans) == 1
    span = port.spans[0]
    assert span.operation == "http.statistics.query"
    assert span.session_ref is None
    assert "private-session-42" not in span.model_dump_json()
