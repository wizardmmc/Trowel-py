"""向 Python 业务组件提供不抛出采集失败的窄遥测端口。"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Protocol

from trowel_py.telemetry.contracts import (
    SCHEMA_VERSION,
    TelemetryBatchRequest,
    TelemetryMetricInput,
    TelemetrySpanInput,
    TelemetrySubmitResult,
)

logger = logging.getLogger(__name__)


class CollectorPort(Protocol):
    """声明 Python emitter 需要的非阻塞批次提交操作。"""

    def submit(self, request: TelemetryBatchRequest) -> TelemetrySubmitResult:
        """尝试把一个批次放入 collector 有界队列。"""

        ...


class TelemetryPort(Protocol):
    """声明业务组件可以发出的两类受控遥测事实。"""

    def emit_span(self, span: TelemetrySpanInput) -> TelemetrySubmitResult:
        """提交一次操作耗时事实。"""

        ...

    def emit_metric(self, metric: TelemetryMetricInput) -> TelemetrySubmitResult:
        """提交一个运行数值样本。"""

        ...


class BufferedTelemetryPort:
    """把单条 Python 事实包装成版本化批次并交给共享 collector。

    Attributes:
        collector: 应用生命周期持有的非阻塞批次接收端。
    """

    def __init__(self, collector: CollectorPort) -> None:
        """保存当前应用实例的 collector。

        Args:
            collector: 不等待 telemetry.db 的批次提交端。
        """

        self.collector = collector

    def emit_span(self, span: TelemetrySpanInput) -> TelemetrySubmitResult:
        """提交一条 span；任何采集异常都转换成 dropped 结果。

        Args:
            span: 已按集中目录构造的操作事实。
        """

        return self._submit(
            source_component=span.component,
            spans=[span.model_dump(mode="json")],
            metrics=[],
        )

    def emit_metric(self, metric: TelemetryMetricInput) -> TelemetrySubmitResult:
        """提交一条 metric；任何采集异常都转换成 dropped 结果。

        Args:
            metric: 已按集中目录构造的数值事实。
        """

        return self._submit(
            source_component=metric.component,
            spans=[],
            metrics=[metric.model_dump(mode="json")],
        )

    def _submit(
        self,
        *,
        source_component: str,
        spans: list[dict[str, object]],
        metrics: list[dict[str, object]],
    ) -> TelemetrySubmitResult:
        """构造单条批次并隔离 collector 的意外失败。

        Args:
            source_component: 发出事实的受控组件。
            spans: 零或一条已编码 span。
            metrics: 零或一条已编码 metric。
        """

        try:
            request = TelemetryBatchRequest(
                batch_id=f"batch-{uuid.uuid4().hex}",
                schema_version=SCHEMA_VERSION,
                source_component=source_component,
                collected_at=datetime.now(UTC),
                spans=spans,
                metrics=metrics,
            )
            return self.collector.submit(request)
        except Exception:
            logger.warning("[telemetry] Python port submission failed", exc_info=True)
            return TelemetrySubmitResult(
                accepted=0,
                rejected=0,
                dropped=len(spans) + len(metrics),
                duplicate=False,
                error_categories={"internal_error": len(spans) + len(metrics)},
            )


class NoopTelemetryPort:
    """在观测底座不可用时保留业务调用形状但不记录事实。"""

    def emit_span(self, span: TelemetrySpanInput) -> TelemetrySubmitResult:
        """忽略 span，并返回不含失败的空结果。

        Args:
            span: 业务组件原本要记录的操作事实。
        """

        del span
        return _noop_result()

    def emit_metric(self, metric: TelemetryMetricInput) -> TelemetrySubmitResult:
        """忽略 metric，并返回不含失败的空结果。

        Args:
            metric: 业务组件原本要记录的数值事实。
        """

        del metric
        return _noop_result()


def _noop_result() -> TelemetrySubmitResult:
    """返回关闭观测时共用的空提交结果。"""

    return TelemetrySubmitResult(
        accepted=0,
        rejected=0,
        dropped=0,
        duplicate=False,
        error_categories={},
    )
