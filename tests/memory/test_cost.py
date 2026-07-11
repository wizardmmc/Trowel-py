"""tests for objective session-cost extraction (slice-040 T5)."""
from __future__ import annotations

from pathlib import Path

from trowel_py.memory.cost import (
    SessionCost,
    extract_cost_from_jsonl,
    extract_session_cost,
)


def test_extract_cost_from_usage() -> None:
    c = extract_session_cost({"input_tokens": 100, "output_tokens": 50}, 3, 1)
    assert c.total_tokens == 150
    assert c.num_turns == 3
    assert c.error_count == 1


def test_extract_cost_handles_none_usage() -> None:
    c = extract_session_cost(None, 0, 0)
    assert c.total_tokens == 0
    assert c.num_turns == 0
    assert c.error_count == 0


def test_extract_cost_tolerates_missing_keys() -> None:
    c = extract_session_cost({}, 5, 2)
    assert c.total_tokens == 0
    assert c.num_turns == 5
    assert c.error_count == 2


def test_cost_is_objective_only_no_pain() -> None:
    # C-5: pain is the agent's semantic judgment; SessionCost must NOT carry it.
    assert "pain" not in SessionCost.__dataclass_fields__


def test_extract_cost_from_jsonl_counts_real_shape(tmp_path: Path) -> None:
    # REAL persisted cc jsonl shape (cc 2.1.197): NO result/init rows; usage
    # lives on assistant rows at message.usage (input cumulative → take last;
    # output incremental → sum); tool_result.is_error inside user rows.
    jsonl = tmp_path / "s.jsonl"
    jsonl.write_text(
        '{"type":"queue-operation","sessionId":"abc","timestamp":"t"}\n'
        '{"type":"user","message":{"content":[{"type":"tool_result","is_error":true}]}}\n'
        '{"type":"assistant","message":{"usage":{"input_tokens":100,'
        '"cache_read_input_tokens":50,"output_tokens":30}}}\n'
        '{"type":"assistant","message":{"usage":{"input_tokens":150,'
        '"cache_read_input_tokens":120,"output_tokens":40}}}\n',
        encoding="utf-8",
    )
    c = extract_cost_from_jsonl(jsonl)
    # last assistant input = 150+120 = 270 (cc cumulative), output summed = 70
    assert c.total_tokens == 270 + 70
    assert c.num_turns == 2  # assistant row count
    assert c.error_count == 1  # the tool_result is_error


def test_extract_cost_from_jsonl_missing_file(tmp_path: Path) -> None:
    # missing file → zero cost (never raises; agent judges from jsonl itself)
    c = extract_cost_from_jsonl(tmp_path / "nope.jsonl")
    assert c.total_tokens == 0
    assert c.num_turns == 0
    assert c.error_count == 0
