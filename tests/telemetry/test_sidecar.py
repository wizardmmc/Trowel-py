"""验证 sidecar 采样器记录当前 RSS 字节和单调 uptime。"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from trowel_py.telemetry.sidecar import SidecarSampler


class RecordingPort:
    """记录测试采样器发出的指标。"""

    def __init__(self) -> None:
        """创建空指标列表。"""

        self.metrics = []

    def emit_metric(self, metric):
        """保存一条指标。"""

        self.metrics.append(metric)
        return None

    def emit_span(self, _span):
        """本测试不接收 span。"""

        return None


def test_sidecar_sampler_records_portable_rss_bytes_without_anomaly_claim() -> None:
    """单次采样只提供两个 gauge，不生成内存异常状态。"""

    times = iter((10.0, 12.5))
    port = RecordingPort()
    sampler = SidecarSampler(
        port,
        process=SimpleNamespace(memory_info=lambda: SimpleNamespace(rss=134_217_728)),
        monotonic=lambda: next(times),
    )

    sampler.sample(datetime(2026, 8, 3, 12, 0, tzinfo=UTC))

    by_name = {metric.name: metric for metric in port.metrics}
    assert by_name["sidecar.uptime_ms"].value == 2_500
    assert by_name["sidecar.rss_bytes"].value == 134_217_728
    assert by_name["sidecar.rss_bytes"].unit == "By"
    assert "anomaly" not in by_name["sidecar.rss_bytes"].model_dump_json()
