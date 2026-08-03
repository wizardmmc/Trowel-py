"""验证被拒绝的私密字段不会进入 telemetry.db 或日志。"""

from __future__ import annotations

import logging
from pathlib import Path

from tests.telemetry.support import batch_request, span_payload
from trowel_py.telemetry.collector import TelemetryCollector
from trowel_py.telemetry.storage import TelemetryDatabase


def test_rejected_private_values_never_reach_database_or_logs(
    tmp_path: Path,
    caplog,
) -> None:
    database = TelemetryDatabase(tmp_path / "telemetry.db")
    database.initialize()
    collector = TelemetryCollector(database.open_writer)
    collector.start()
    private = span_payload()
    private.update(
        {
            "prompt": "SECRET_PROMPT_443",
            "tool_result": "SECRET_TOOL_RESULT_443",
            "credential": "sk-secret-443",
            "path": "/Users/example/private-443.txt",
        }
    )

    with caplog.at_level(logging.WARNING):
        result = collector.submit(batch_request(spans=[private]))
        collector.close(timeout_seconds=1.0)

    disk = b"".join(
        path.read_bytes()
        for path in (
            database.path,
            database.path.with_name(database.path.name + "-wal"),
            database.path.with_name(database.path.name + "-shm"),
        )
        if path.exists()
    )
    combined = disk + caplog.text.encode("utf-8")

    assert result.rejected == 1
    assert result.error_categories == {"privacy_field": 1}
    for secret in (
        b"SECRET_PROMPT_443",
        b"SECRET_TOOL_RESULT_443",
        b"sk-secret-443",
        b"/Users/example/private-443.txt",
    ):
        assert secret not in combined
