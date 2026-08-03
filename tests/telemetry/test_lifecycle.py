"""验证应用生命周期创建隔离遥测库并有界关闭 collector。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from trowel_py.app import create_app
from trowel_py.telemetry import lifecycle
from trowel_py.telemetry.checkpoint import CheckpointCloseReport
from trowel_py.telemetry.collector import CollectorCloseReport


def test_app_lifespan_owns_telemetry_database_under_application_root(
    tmp_path: Path,
    monkeypatch,
) -> None:
    data_root = tmp_path / "isolated-data"
    monkeypatch.setenv("TROWEL_DATA_ROOT", str(data_root))

    with TestClient(create_app()) as client:
        response = client.get("/api/health")
        assert response.status_code == 200
        assert client.app.state.telemetry_collector.snapshot().running is True

    assert (data_root / "telemetry.db").exists()
    assert client.app.state.telemetry_close_report.drained is True
    assert client.app.state.telemetry_checkpoint_close_report.closed is True


def test_startup_failure_closes_partially_started_background_workers(
    tmp_path: Path,
    monkeypatch,
) -> None:
    closed: list[str] = []

    class RecordingCheckpointer:
        def start(self) -> None:
            pass

        def request(self) -> None:
            pass

        def close(self, *, timeout_seconds: float) -> CheckpointCloseReport:
            closed.append("checkpointer")
            return CheckpointCloseReport(True, 0, 0, 0)

    class FailingCollector:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def start(self) -> None:
            raise RuntimeError("collector startup failed")

        def close(self, *, timeout_seconds: float) -> CollectorCloseReport:
            closed.append("collector")
            return CollectorCloseReport(True, 0, 0)

    monkeypatch.setenv("TROWEL_DATA_ROOT", str(tmp_path / "isolated-data"))
    monkeypatch.setattr(
        lifecycle,
        "TelemetryCheckpointer",
        lambda _checkpoint: RecordingCheckpointer(),
    )
    monkeypatch.setattr(lifecycle, "TelemetryCollector", FailingCollector)
    app = SimpleNamespace(state=SimpleNamespace())

    lifecycle.start_telemetry(app)

    assert closed == ["collector", "checkpointer"]
    assert app.state.telemetry_collector is None
    assert app.state.telemetry_checkpointer is None
    assert app.state.telemetry_close_report.drained is True
    assert app.state.telemetry_checkpoint_close_report.closed is True


def test_stop_closes_checkpointer_even_when_collector_is_unavailable() -> None:
    closed: list[float] = []

    class RecordingCheckpointer:
        def close(self, *, timeout_seconds: float) -> CheckpointCloseReport:
            closed.append(timeout_seconds)
            return CheckpointCloseReport(True, 0, 0, 0)

    app = SimpleNamespace(
        state=SimpleNamespace(
            telemetry_collector=None,
            telemetry_checkpointer=RecordingCheckpointer(),
            telemetry_port=object(),
            telemetry_close_report=None,
            telemetry_checkpoint_close_report=None,
        )
    )

    report = lifecycle.stop_telemetry(app, timeout_seconds=0.25)

    assert report is None
    assert closed == [0.25]
    assert app.state.telemetry_checkpoint_close_report.closed is True
