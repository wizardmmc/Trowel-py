"""验证调用列表分页、跨 trace 关联和坏图降级。"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

from tests.telemetry.support import BASE_TIME, batch_request, span_payload
from trowel_py.statistics.calls.repository import CallStatisticsReader
from trowel_py.statistics.calls.service import build_call_detail, build_call_list
from trowel_py.statistics.window import StatisticsWindow
from trowel_py.telemetry.contracts import prepare_batch
from trowel_py.telemetry.storage import TelemetryDatabase


def _window() -> StatisticsWindow:
    return StatisticsWindow(
        start=BASE_TIME - timedelta(hours=1),
        end=BASE_TIME + timedelta(days=1),
        timezone="UTC",
    )


def _database(tmp_path: Path, spans: list[dict[str, object]]) -> TelemetryDatabase:
    database = TelemetryDatabase(tmp_path / "telemetry.db")
    database.initialize()
    with database.open_writer() as writer:
        writer.write_batches([prepare_batch(batch_request(spans=spans))])
    return database


def _linked_trace_spans() -> list[dict[str, object]]:
    renderer = span_payload(
        1,
        component="renderer",
        operation="http.agent.messages",
        duration_ms=30,
    )
    fastapi = span_payload(
        2,
        component="fastapi",
        operation="http.agent.messages",
        started_at=BASE_TIME + timedelta(milliseconds=2),
        duration_ms=20,
    )
    fastapi.update(
        {
            "trace_id": renderer["trace_id"],
            "parent_span_id": renderer["span_id"],
        }
    )
    sqlite = span_payload(
        3,
        component="sqlite",
        operation="sqlite.sessions.read",
        started_at=BASE_TIME + timedelta(milliseconds=5),
        duration_ms=3,
    )
    sqlite.update(
        {
            "trace_id": renderer["trace_id"],
            "parent_span_id": fastapi["span_id"],
        }
    )
    turn = span_payload(
        4,
        component="agent_host",
        operation="agent.turn",
        started_at=BASE_TIME + timedelta(milliseconds=3),
        duration_ms=100,
    )
    turn.update(
        {
            "trace_id": renderer["trace_id"],
            "parent_span_id": fastapi["span_id"],
            "runtime": "codex",
            "session_ref": "session-private",
            "call_ref": "turn-private",
        }
    )
    runtime_call = span_payload(
        6,
        component="runtime",
        operation="runtime.call",
        started_at=BASE_TIME + timedelta(milliseconds=4),
        duration_ms=96,
    )
    runtime_call.update(
        {
            "runtime": "codex",
            "session_ref": "session-private",
            "call_ref": "turn-private",
            "attributes": {
                "quality": "partial",
                "transport": "stdio",
                "black_box": True,
            },
            "links": [{"trace_id": turn["trace_id"], "span_id": turn["span_id"]}],
        }
    )
    mcp = span_payload(
        5,
        component="mcp",
        operation="mcp.tools.call",
        started_at=BASE_TIME + timedelta(milliseconds=10),
        duration_ms=80,
    )
    mcp.update(
        {
            "runtime": "codex",
            "session_ref": "session-private",
            "call_ref": "tool-private",
            "attributes": {
                "quality": "reliable",
                "retry_count": 2,
                "sampled": False,
                "transport": "stdio",
            },
            "links": [
                {
                    "trace_id": runtime_call["trace_id"],
                    "span_id": runtime_call["span_id"],
                }
            ],
        }
    )
    return [renderer, fastapi, sqlite, turn, runtime_call, mcp]


def test_call_list_filters_and_uses_stable_keyset_cursor(tmp_path: Path) -> None:
    spans = _linked_trace_spans()
    database = _database(tmp_path, spans)
    reader = CallStatisticsReader(database)

    first = build_call_list(reader, _window(), limit=2)
    inserted = span_payload(
        20,
        component="runtime",
        operation="runtime.call",
        started_at=BASE_TIME + timedelta(hours=1),
        duration_ms=200,
    )
    inserted.update({"runtime": "codex"})
    with database.open_writer() as writer:
        writer.write_batches(
            [prepare_batch(batch_request("batch-newer", spans=[inserted]))]
        )
    second = build_call_list(reader, _window(), limit=2, cursor=first.next_cursor)
    filtered = build_call_list(
        reader,
        _window(),
        component="mcp",
        runtime="codex",
        status="ok",
        minimum_duration_ms=50,
        limit=20,
    )

    assert len(first.items) == 2
    assert first.next_cursor is not None
    assert {item.span_id for item in first.items}.isdisjoint(
        item.span_id for item in second.items
    )
    assert [item.operation for item in filtered.items] == ["mcp.tools.call"]
    assert filtered.items[0].quality == "reliable"
    assert first.items[0].started_at > first.items[1].started_at


def test_detail_follows_links_both_directions_and_hides_private_fields(
    tmp_path: Path,
) -> None:
    spans = _linked_trace_spans()
    database = _database(tmp_path, spans)
    connection = database.connect_reader()
    try:
        connection.execute(
            "UPDATE raw_spans SET attributes_json = ? WHERE span_id = ?",
            (
                json.dumps(
                    {
                        "quality": "partial",
                        "retry_count": 2,
                        "private_numeric": 123456,
                    }
                ),
                bytes.fromhex(str(spans[-1]["span_id"])),
            ),
        )
        connection.commit()
    finally:
        connection.close()
    reader = CallStatisticsReader(database)

    detail = build_call_detail(reader, str(spans[0]["trace_id"]))

    assert detail is not None
    assert {span.component for span in detail.spans} == {
        "renderer",
        "fastapi",
        "sqlite",
        "agent_host",
        "runtime",
        "mcp",
    }
    assert detail.quality == "partial"
    assert {gap.code for gap in detail.unavailable} == {"native_runtime_black_box"}
    mcp = next(span for span in detail.spans if span.component == "mcp")
    assert mcp.attributes == {"retry_count": 2}
    assert mcp.links[0].available is True
    rendered = detail.model_dump_json()
    assert "session-private" not in rendered
    assert "turn-private" not in rendered
    assert "tool-private" not in rendered
    assert "sampled" not in rendered


def test_detail_status_uses_root_terminal_without_promoting_linked_errors(
    tmp_path: Path,
) -> None:
    """已处理的子错误或关联 trace 错误不能覆盖根调用终态。"""

    spans = _linked_trace_spans()
    sqlite = next(span for span in spans if span["component"] == "sqlite")
    mcp = next(span for span in spans if span["component"] == "mcp")
    sqlite["status"] = "error"
    mcp["status"] = "error"
    database = _database(tmp_path, spans)

    detail = build_call_detail(
        CallStatisticsReader(database),
        str(spans[0]["trace_id"]),
    )

    assert detail is not None
    assert detail.status == "ok"
    assert {span.operation for span in detail.spans if span.status == "error"} == {
        "sqlite.sessions.read",
        "mcp.tools.call",
    }


def test_detail_downgrades_cycles_broken_parents_and_duplicate_links(
    tmp_path: Path,
) -> None:
    first = span_payload(31, component="runtime", operation="runtime.call")
    second = span_payload(
        32,
        component="runtime",
        operation="runtime.tool",
        started_at=BASE_TIME + timedelta(milliseconds=1),
    )
    second["trace_id"] = first["trace_id"]
    first["parent_span_id"] = second["span_id"]
    second["parent_span_id"] = first["span_id"]
    first["links"] = [
        {"trace_id": f"{99:032x}", "span_id": f"{99:016x}"},
        {"trace_id": f"{99:032x}", "span_id": f"{99:016x}"},
    ]
    orphan = span_payload(
        33,
        component="sqlite",
        operation="sqlite.query",
        started_at=BASE_TIME + timedelta(milliseconds=2),
    )
    orphan["trace_id"] = first["trace_id"]
    orphan["parent_span_id"] = f"{98:016x}"
    database = _database(tmp_path, [first, second, orphan])

    detail = build_call_detail(
        CallStatisticsReader(database),
        str(first["trace_id"]),
    )

    assert detail is not None
    assert detail.quality == "partial"
    assert {gap.code for gap in detail.unavailable} == {
        "duplicate_link",
        "missing_link_target",
        "missing_parent",
        "parent_cycle",
    }
    assert all(span.parent_span_id is None for span in detail.spans)
    assert len(detail.spans[0].links) == 1


def test_call_query_rejects_invalid_filters_and_cursor(tmp_path: Path) -> None:
    database = _database(tmp_path, [span_payload()])
    reader = CallStatisticsReader(database)

    for kwargs in (
        {"component": "private-component"},
        {"operation": "dynamic/path/123"},
        {"runtime": "other"},
        {"status": "completed"},
        {"minimum_duration_ms": -1},
        {"cursor": "not-a-cursor"},
    ):
        try:
            build_call_list(reader, _window(), **kwargs)
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid query accepted: {kwargs}")
