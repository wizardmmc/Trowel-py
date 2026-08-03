"""验证独立 checkpointer 合并请求并有界结束。"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from tests.telemetry.support import batch_request, span_payload
from trowel_py.telemetry.checkpoint import TelemetryCheckpointer
from trowel_py.telemetry.contracts import prepare_batch
from trowel_py.telemetry.storage import TelemetryDatabase


def test_checkpointer_coalesces_requests_off_the_writer_thread() -> None:
    called_from: list[str] = []
    completed = threading.Event()

    def checkpoint() -> tuple[int, int, int]:
        called_from.append(threading.current_thread().name)
        completed.set()
        return (0, 0, 0)

    checkpointer = TelemetryCheckpointer(checkpoint)
    checkpointer.start()
    for _ in range(20):
        checkpointer.request()

    assert completed.wait(1.0)
    report = checkpointer.close(timeout_seconds=1.0)

    assert report.closed is True
    assert report.completed >= 1
    assert report.requested == 20
    assert called_from
    assert set(called_from) == {"trowel-telemetry-checkpointer"}


def test_checkpointer_close_is_bounded_when_checkpoint_is_stuck() -> None:
    gate = threading.Event()

    def checkpoint() -> tuple[int, int, int]:
        gate.wait(5.0)
        return (0, 0, 0)

    checkpointer = TelemetryCheckpointer(checkpoint)
    checkpointer.start()
    checkpointer.request()
    time.sleep(0.02)

    started = time.perf_counter()
    report = checkpointer.close(timeout_seconds=0.02)
    elapsed = time.perf_counter() - started
    gate.set()

    assert report.closed is False
    assert elapsed < 0.1


def test_checkpointer_merges_real_wal_frames_from_an_open_writer(
    tmp_path: Path,
) -> None:
    database = TelemetryDatabase(tmp_path / "telemetry.db")
    database.initialize()
    checkpoint_results: list[tuple[int, int, int]] = []

    def checkpoint() -> tuple[int, int, int]:
        result = database.checkpoint()
        checkpoint_results.append(result)
        return result

    with database.open_writer() as writer:
        for index in range(1, 51):
            writer.write_batches(
                [
                    prepare_batch(
                        batch_request(
                            batch_id=f"batch-wal-{index:04d}",
                            spans=[span_payload(index)],
                        )
                    )
                ]
            )
        assert database.path.with_name("telemetry.db-wal").stat().st_size > 0

        checkpointer = TelemetryCheckpointer(checkpoint)
        checkpointer.start()
        checkpointer.request()
        report = checkpointer.close(timeout_seconds=1.0)

    assert report.closed is True
    assert report.completed == 1
    assert report.failed == 0
    assert checkpoint_results
    busy, wal_frames, checkpointed_frames = checkpoint_results[-1]
    assert busy == 0
    assert wal_frames > 0
    assert checkpointed_frames == wal_frames
