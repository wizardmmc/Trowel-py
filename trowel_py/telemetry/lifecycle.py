"""在 FastAPI 生命周期内组装和有界关闭本地观测底座。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from trowel_py.telemetry.checkpoint import TelemetryCheckpointer
from trowel_py.telemetry.collector import CollectorCloseReport, TelemetryCollector
from trowel_py.telemetry.exit_markers import import_exit_marker
from trowel_py.telemetry.port import BufferedTelemetryPort, NoopTelemetryPort
from trowel_py.telemetry.storage import (
    TelemetryDatabase,
    resolve_telemetry_database_path,
)

logger = logging.getLogger(__name__)


def start_telemetry(app: Any) -> None:
    """创建 telemetry.db、reader、collector 和 Python emitter。

    初始化失败只关闭观测能力，不能阻止 Agent、Memory 或应用启动。

    Args:
        app: 当前 FastAPI 应用，其 state 持有观测组件。
    """

    app.state.telemetry_database = None
    app.state.telemetry_reader = None
    app.state.telemetry_collector = None
    app.state.telemetry_checkpointer = None
    app.state.telemetry_port = NoopTelemetryPort()
    app.state.telemetry_close_report = None
    app.state.telemetry_checkpoint_close_report = None
    app.state.telemetry_exit_marker_import = None
    try:
        database = TelemetryDatabase(resolve_telemetry_database_path())
        database.initialize()
        app.state.telemetry_database = database
        desktop_data_dir = str(getattr(app.state, "desktop_data_dir", "")).strip()
        if desktop_data_dir:
            data_directory = Path(desktop_data_dir)
            if data_directory.resolve() == database.path.parent.resolve():
                app.state.telemetry_exit_marker_import = {
                    marker_name: import_exit_marker(
                        database,
                        data_directory / marker_name,
                    )
                    for marker_name in ("resource-exit.json", "sidecar-exit.json")
                }
            else:
                logger.warning(
                    "[telemetry] exit marker import skipped: desktop and telemetry "
                    "data roots differ"
                )
        reader = database.reader()
        app.state.telemetry_reader = reader
        checkpointer = TelemetryCheckpointer(database.checkpoint)
        app.state.telemetry_checkpointer = checkpointer
        checkpointer.start()
        collector = TelemetryCollector(
            database.open_writer,
            recent_batches=reader.recent_batch_fingerprints(),
            checkpoint_requester=checkpointer.request,
        )
        app.state.telemetry_collector = collector
        collector.start()
        app.state.telemetry_port = BufferedTelemetryPort(collector)
    except Exception:
        logger.warning("[telemetry] observability disabled after startup failure", exc_info=True)
        stop_telemetry(app, timeout_seconds=1.0)
        app.state.telemetry_database = None
        app.state.telemetry_reader = None
        app.state.telemetry_collector = None
        app.state.telemetry_checkpointer = None


def stop_telemetry(
    app: Any,
    *,
    timeout_seconds: float = 1.0,
) -> CollectorCloseReport | None:
    """在上限内 drain collector，并始终把应用 port 切换成 no-op。

    Args:
        app: 当前 FastAPI 应用。
        timeout_seconds: writer 最多占用退出链的秒数。

    Returns:
        collector 存在时的关闭报告；观测未启动时为 None。
    """

    collector = getattr(app.state, "telemetry_collector", None)
    app.state.telemetry_port = NoopTelemetryPort()
    report: CollectorCloseReport | None = None
    if collector is not None:
        try:
            report = collector.close(timeout_seconds=timeout_seconds)
        except Exception:
            logger.warning("[telemetry] collector shutdown failed", exc_info=True)
        else:
            app.state.telemetry_close_report = report
    checkpointer = getattr(app.state, "telemetry_checkpointer", None)
    if checkpointer is not None:
        try:
            checkpoint_report = checkpointer.close(timeout_seconds=timeout_seconds)
        except Exception:
            logger.warning("[telemetry] checkpointer shutdown failed", exc_info=True)
        else:
            app.state.telemetry_checkpoint_close_report = checkpoint_report
            if not checkpoint_report.closed:
                logger.warning("[telemetry] checkpoint shutdown timed out")
    if report is not None and not report.drained:
        logger.warning(
            "[telemetry] shutdown timed out; dropped=%s remaining=%s",
            report.dropped,
            report.remaining_records,
        )
    return report
