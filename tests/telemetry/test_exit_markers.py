"""验证 Electron 最终退出标记的幂等导入和失败隔离。"""

from __future__ import annotations

import json
from pathlib import Path

from trowel_py.telemetry.exit_markers import import_exit_marker
from trowel_py.telemetry.storage import TelemetryDatabase


def _write_marker(
    path: Path,
    *,
    exit_reason: str = "app_exit",
    completed_at: str = "2026-08-03T01:02:04.250Z",
) -> None:
    """写入一份不含原始应用身份的 v2 退出标记。

    Args:
        path: 标记文件路径。
        exit_reason: 应用退出或 sidecar 异常退出。
        completed_at: 用于生成不同幂等身份的结束时刻。
    """

    path.write_text(
        json.dumps(
            {
                "version": 2,
                "app_instance_id": "a" * 20,
                "requested_at": "2026-08-03T01:02:03.000Z",
                "completed_at": completed_at,
                "exit_reason": exit_reason,
                "exit_mode": "forced",
                "process_tree_result": "needs_reconcile",
                "remaining_resource_count": 2,
            }
        ),
        encoding="utf-8",
    )


def test_import_exit_marker_is_durable_archived_and_idempotent(
    tmp_path: Path,
) -> None:
    """成功导入后归档原标记；同一事实重放不会增加遥测行。"""

    database = TelemetryDatabase(tmp_path / "telemetry.db")
    database.initialize()
    marker = tmp_path / "resource-exit.json"
    _write_marker(marker)

    first = import_exit_marker(database, marker)
    _write_marker(marker)
    second = import_exit_marker(database, marker)

    assert first.status == "imported"
    assert first.inserted_records == 5
    assert second.status == "duplicate"
    assert database.reader().raw_counts() == {"spans": 2, "metrics": 3}
    assert not marker.exists()
    assert first.archive_path is not None
    assert first.archive_path.is_file()


def test_invalid_exit_marker_is_left_for_diagnosis(tmp_path: Path) -> None:
    """损坏标记不能阻断启动，也不能被移走掩盖问题。"""

    database = TelemetryDatabase(tmp_path / "telemetry.db")
    database.initialize()
    marker = tmp_path / "resource-exit.json"
    marker.write_text('{"version":2,"requested_at":"private/path"}', encoding="utf-8")

    report = import_exit_marker(database, marker)

    assert report.status == "invalid"
    assert report.inserted_records == 0
    assert marker.exists()
    assert database.reader().raw_counts() == {"spans": 0, "metrics": 0}


def test_sidecar_abnormal_marker_uses_smaller_fixed_fact_set(tmp_path: Path) -> None:
    """sidecar 异常退出只导入自身终态，不伪造一次完整应用关闭。"""

    database = TelemetryDatabase(tmp_path / "telemetry.db")
    database.initialize()
    marker = tmp_path / "sidecar-exit.json"
    _write_marker(marker, exit_reason="sidecar_abnormal")

    report = import_exit_marker(database, marker)

    assert report.status == "imported"
    assert report.inserted_records == 3
    assert database.reader().raw_counts() == {"spans": 1, "metrics": 2}


def test_exit_marker_archive_keeps_only_recent_files(tmp_path: Path) -> None:
    """历史退出标记归档有固定上限，不能随启动次数无限增长。"""

    database = TelemetryDatabase(tmp_path / "telemetry.db")
    database.initialize()
    archive = tmp_path / "exit-markers"
    archive.mkdir()
    for index in range(70):
        (archive / f"exit-old-{index:02d}.json").write_text("{}", encoding="utf-8")
    marker = tmp_path / "resource-exit.json"
    _write_marker(marker)

    report = import_exit_marker(database, marker)

    assert report.status == "imported"
    assert len(tuple(archive.glob("exit-*.json"))) == 64
