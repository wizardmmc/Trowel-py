"""固定遥测批次的版本、白名单和部分接受语义。"""

from __future__ import annotations

from tests.telemetry.support import batch_request, metric_payload, span_payload
from trowel_py.telemetry.contracts import prepare_batch


def test_prepare_batch_accepts_span_and_metric_with_fixed_width_ids() -> None:
    prepared = prepare_batch(
        batch_request(
            spans=[span_payload(7)],
            metrics=[metric_payload(7)],
        )
    )

    assert prepared.accepted_count == 2
    assert prepared.rejected_count == 0
    assert len(prepared.spans[0].trace_id) == 16
    assert len(prepared.spans[0].span_id) == 8


def test_prepare_batch_rejects_private_field_without_losing_valid_records() -> None:
    private_span = span_payload(2)
    private_span["prompt"] = "never persist this"
    prepared = prepare_batch(
        batch_request(spans=[span_payload(1), private_span])
    )

    assert prepared.accepted_count == 1
    assert prepared.rejected_count == 1
    assert prepared.error_categories == {"privacy_field": 1}
    assert "never persist this" not in repr(prepared)


def test_prepare_batch_rejects_uncontrolled_dimensions_and_absolute_paths() -> None:
    unknown_operation = span_payload(3)
    unknown_operation["operation"] = "dynamic./Users/example/private.sql"
    path_attribute = span_payload(4)
    path_attribute["attributes"] = {
        "quality": "reliable",
        "path": "/Users/example/private.txt",
    }

    prepared = prepare_batch(
        batch_request(spans=[unknown_operation, path_attribute])
    )

    assert prepared.accepted_count == 0
    assert prepared.rejected_count == 2
    assert prepared.error_categories == {
        "privacy_field": 1,
        "unsupported_dimension": 1,
    }


def test_prepare_batch_accepts_models_reported_by_runtimes() -> None:
    """模型目录变化时保留 runtime 实际回报的模型名。"""

    span = span_payload(5)
    span.update({"runtime": "codex", "model": "future-model-native"})
    metric = metric_payload(5)
    metric.update({"runtime": "claude_code", "model": "glm-actual"})

    prepared = prepare_batch(batch_request(spans=[span], metrics=[metric]))

    assert prepared.accepted_count == 2
    assert prepared.rejected_count == 0
    assert prepared.spans[0].model == "future-model-native"
    assert prepared.metrics[0].model == "glm-actual"


def test_prepare_batch_rejects_malformed_runtime_model_names() -> None:
    """动态模型名仍不能携带空白、控制字符或无界正文。"""

    invalid_models = ("", " model-with-padding ", "model\nname", "m" * 257)
    spans = []
    for index, model in enumerate(invalid_models, start=10):
        span = span_payload(index)
        span.update({"runtime": "codex", "model": model})
        spans.append(span)

    prepared = prepare_batch(batch_request(spans=spans))

    assert prepared.accepted_count == 0
    assert prepared.rejected_count == len(invalid_models)
    assert prepared.error_categories == {
        "unsupported_dimension": len(invalid_models),
    }


def test_normal_and_backfill_batches_have_distinct_limits() -> None:
    normal = prepare_batch(
        batch_request(
            batch_id="batch-normal-limit",
            spans=[span_payload(index + 1) for index in range(251)],
        )
    )
    backfill = prepare_batch(
        batch_request(
            batch_id="batch-backfill-limit",
            spans=[span_payload(index + 1) for index in range(1000)],
            mode="backfill",
        )
    )

    assert normal.accepted_count == 0
    assert normal.rejected_count == 251
    assert normal.error_categories == {"batch_too_large": 251}
    assert backfill.accepted_count == 1000
