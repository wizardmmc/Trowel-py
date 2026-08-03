"""把 sidecar 结束后由 Electron 写出的退出事实幂等导入遥测库。"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from trowel_py.telemetry.contracts import (
    SCHEMA_VERSION,
    TelemetryAttributes,
    TelemetryBatchRequest,
    TelemetryMetricInput,
    TelemetrySpanInput,
    prepare_batch,
)
from trowel_py.telemetry.storage import TelemetryDatabase

EXIT_MARKER_ARCHIVE_LIMIT = 64
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExitMarkerImportReport:
    """描述一次退出标记导入结果。

    Attributes:
        status: imported、duplicate、invalid 或 failed。
        inserted_records: 本次新写入的 span 与 metric 数量。
        archive_path: 成功持久化后保存原始标记的本地路径。
    """

    status: Literal["imported", "duplicate", "invalid", "failed"]
    inserted_records: int = 0
    archive_path: Path | None = None


@dataclass(frozen=True)
class _ExitMarker:
    """保存通过版本与字段校验的 Host 退出事实。

    Attributes:
        app_instance_id: Host 已做不可逆摘要的应用实例身份。
        requested_at: Host 收到退出请求的时刻。
        completed_at: Host 完成进程树核验的时刻。
        exit_mode: cooperative 或 forced。
        process_tree_result: closed 或 needs_reconcile。
        remaining_resource_count: 仍存活或无法核验的资源数。
        quality: v2 为 reliable，兼容 v1 推导时为 partial。
        exit_reason: 应用主动退出或 sidecar 异常结束。
    """

    app_instance_id: str
    requested_at: datetime
    completed_at: datetime
    exit_mode: Literal["cooperative", "forced"]
    process_tree_result: Literal["closed", "needs_reconcile"]
    remaining_resource_count: int
    quality: Literal["reliable", "partial"]
    exit_reason: Literal["app_exit", "sidecar_abnormal"]


def import_exit_marker(
    database: TelemetryDatabase,
    marker_path: Path,
) -> ExitMarkerImportReport:
    """同步持久化一份小型退出事实，成功后把原文件移入有界数据根。

    导入发生在 collector 启动前，直接事务写入能保证归档前已经落库。同一标记生成
    固定 batch、trace、span 和 metric 身份，重复启动只会命中幂等去重。

    Args:
        database: 已初始化但尚未启动 collector 的 telemetry.db 所有者。
        marker_path: Electron Host 写出的 ``resource-exit.json`` 路径。

    Returns:
        不抛出标记或写库异常的导入报告。
    """

    if not marker_path.is_file():
        return ExitMarkerImportReport(status="invalid")
    try:
        raw = marker_path.read_text(encoding="utf-8")
        payload = json.loads(raw)
        marker = _parse_marker(payload)
        batch_id = _batch_id(marker)
        request = _build_request(marker, batch_id)
        prepared = prepare_batch(request)
        if prepared.rejected_count:
            return ExitMarkerImportReport(status="invalid")
        with database.open_writer() as writer:
            write_report = writer.write_batches([prepared])
        archive_path = _archive_marker(marker_path, batch_id)
    except (OSError, ValueError, TypeError):
        return ExitMarkerImportReport(status="invalid")
    except Exception:
        return ExitMarkerImportReport(status="failed")
    status: Literal["imported", "duplicate"] = (
        "duplicate" if write_report.duplicate_batches else "imported"
    )
    return ExitMarkerImportReport(
        status=status,
        inserted_records=write_report.inserted_records,
        archive_path=archive_path,
    )


def _parse_marker(payload: object) -> _ExitMarker:
    """校验 v2 标记，并只为历史 v1 标记提供保守兼容。

    Args:
        payload: 从 JSON 解码的未知对象。

    Returns:
        可安全导入的固定退出事实。

    Raises:
        ValueError: 版本、时间、状态或数量不合法。
    """

    if not isinstance(payload, dict):
        raise ValueError("exit marker must be an object")
    version = payload.get("version")
    instance_id = payload.get("app_instance_id")
    remaining = payload.get("remaining_resource_count")
    if not isinstance(instance_id, str) or not instance_id:
        raise ValueError("exit marker instance is missing")
    if not isinstance(remaining, int) or isinstance(remaining, bool) or remaining < 0:
        raise ValueError("exit marker remaining count is invalid")
    if version == 2:
        requested = _parse_time(payload.get("requested_at"))
        completed = _parse_time(payload.get("completed_at"))
        exit_mode = payload.get("exit_mode")
        process_tree_result = payload.get("process_tree_result")
        exit_reason = payload.get("exit_reason", "app_exit")
        quality: Literal["reliable", "partial"] = "reliable"
    elif version == 1:
        completed = _parse_time(payload.get("updated_at"))
        requested = completed
        process_tree_result = payload.get("status")
        exit_mode = "forced" if process_tree_result == "needs_reconcile" else "cooperative"
        quality = "partial"
        exit_reason = "app_exit"
    else:
        raise ValueError("unsupported exit marker version")
    if exit_mode not in {"cooperative", "forced"}:
        raise ValueError("unsupported exit mode")
    if process_tree_result not in {"closed", "needs_reconcile"}:
        raise ValueError("unsupported process tree result")
    if exit_reason not in {"app_exit", "sidecar_abnormal"}:
        raise ValueError("unsupported exit reason")
    if completed < requested:
        raise ValueError("exit marker interval is negative")
    return _ExitMarker(
        app_instance_id=instance_id,
        requested_at=requested,
        completed_at=completed,
        exit_mode=exit_mode,
        process_tree_result=process_tree_result,
        remaining_resource_count=remaining,
        quality=quality,
        exit_reason=exit_reason,
    )


def _parse_time(value: object) -> datetime:
    """解析带时区 ISO 时间。

    Args:
        value: 退出标记里的未知时间字段。

    Returns:
        保留原时区的 datetime。

    Raises:
        ValueError: 字段缺失或不含时区。
    """

    if not isinstance(value, str):
        raise ValueError("exit marker time is missing")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("exit marker time must include timezone")
    return parsed


def _batch_id(marker: _ExitMarker) -> str:
    """从稳定退出事实生成不含原始身份的批次 ID。

    Args:
        marker: 已校验退出标记。

    Returns:
        满足遥测批次白名单的固定摘要。
    """

    identity = "|".join(
        (
            marker.app_instance_id,
            marker.requested_at.isoformat(),
            marker.completed_at.isoformat(),
            marker.process_tree_result,
            marker.exit_reason,
        )
    )
    return f"exit-{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:32]}"


def _build_request(marker: _ExitMarker, batch_id: str) -> TelemetryBatchRequest:
    """把退出事实转换成固定数量的耗时 span 和可聚合指标。

    Args:
        marker: 已校验退出标记。
        batch_id: 当前事实的稳定幂等批次 ID。

    Returns:
        可交给通用隐私校验器准备的 backfill 批次。
    """

    digest = hashlib.sha256(batch_id.encode("utf-8")).hexdigest()
    status = (
        "ok"
        if marker.exit_reason == "app_exit" and marker.process_tree_result == "closed"
        else "error"
    )
    attributes = TelemetryAttributes(
        quality=marker.quality,
        sampled=False,
        exit_mode=marker.exit_mode,
        process_tree_result=marker.process_tree_result,
    )
    exit_operation = "desktop.exit" if marker.exit_reason == "app_exit" else "sidecar.exit"
    exit_component = "electron" if marker.exit_reason == "app_exit" else "sidecar"
    exit_span = TelemetrySpanInput(
        trace_id=digest[:32],
        span_id=digest[32:48],
        parent_span_id=None,
        started_at=marker.requested_at,
        ended_at=marker.completed_at,
        component=exit_component,
        operation=exit_operation,
        status=status,
        runtime=None,
        model=None,
        session_ref=None,
        call_ref=None,
        attributes=attributes,
        links=[],
    )
    resource_span = TelemetrySpanInput(
        trace_id=digest[:32],
        span_id=digest[48:64],
        parent_span_id=None,
        started_at=marker.requested_at,
        ended_at=marker.completed_at,
        component="runtime",
        operation="resource.app.close",
        status=status,
        runtime=None,
        model=None,
        session_ref=None,
        call_ref=None,
        attributes=attributes,
        links=[],
    )
    metrics = [
        TelemetryMetricInput(
            metric_id="exit-terminal",
            observed_at=marker.completed_at,
            component=exit_component,
            name=(
                "desktop.exit_terminal"
                if marker.exit_reason == "app_exit"
                else "sidecar.abnormal_exit"
            ),
            kind="counter",
            unit="1",
            value=1,
            status=status,
            operation=exit_operation,
            attributes=attributes,
        ),
        TelemetryMetricInput(
            metric_id="remaining-resources",
            observed_at=marker.completed_at,
            component=exit_component,
            name="desktop.remaining_resources",
            kind="gauge",
            unit="1",
            value=marker.remaining_resource_count,
            status=status,
            operation=exit_operation,
            attributes=attributes,
        ),
        TelemetryMetricInput(
            metric_id="app-owner-remaining",
            observed_at=marker.completed_at,
            component="runtime",
            name="resource.remaining",
            kind="gauge",
            unit="1",
            value=marker.remaining_resource_count,
            status=status,
            operation="resource.app.close",
            attributes=attributes,
        ),
    ]
    spans = [exit_span.model_dump(mode="json")]
    if marker.exit_reason == "app_exit":
        spans.append(resource_span.model_dump(mode="json"))
    metric_payloads = [metric.model_dump(mode="json") for metric in metrics]
    if marker.exit_reason != "app_exit":
        metric_payloads = metric_payloads[:2]
    return TelemetryBatchRequest(
        batch_id=batch_id,
        schema_version=SCHEMA_VERSION,
        source_component="electron",
        collected_at=marker.completed_at,
        mode="backfill",
        spans=spans,
        metrics=metric_payloads,
    )


def _archive_marker(marker_path: Path, batch_id: str) -> Path:
    """把已持久化标记原子移入同一数据根的归档目录。

    Args:
        marker_path: Host 写出的当前标记路径。
        batch_id: 只含稳定摘要的归档身份。

    Returns:
        归档后的完整路径。
    """

    archive_dir = marker_path.parent / "exit-markers"
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive_path = archive_dir / f"{batch_id}.json"
    os.replace(marker_path, archive_path)
    _prune_archive(archive_dir)
    return archive_path


def _prune_archive(archive_dir: Path) -> None:
    """尽力把退出标记归档压到固定上限，清理失败不否定已经落库的事实。

    Args:
        archive_dir: 只保存去敏退出标记的本地归档目录。
    """

    try:
        archived = sorted(
            archive_dir.glob("exit-*.json"),
            key=lambda path: (path.stat().st_mtime_ns, path.name),
            reverse=True,
        )
        for expired in archived[EXIT_MARKER_ARCHIVE_LIMIT:]:
            expired.unlink(missing_ok=True)
    except OSError:
        logger.debug("[telemetry] exit marker archive pruning failed", exc_info=True)
