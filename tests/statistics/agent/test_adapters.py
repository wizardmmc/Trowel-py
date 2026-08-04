"""用 L01 脱敏真实记录验证双 runtime 统计口径。"""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

import trowel_py.statistics.agent.codex as codex_adapter
from trowel_py.statistics.agent.claude import (
    analyze_claude_binding,
    analyze_claude_binding_many,
)
from trowel_py.statistics.agent.codex import (
    analyze_codex_session,
    analyze_codex_session_many,
)
from trowel_py.statistics.agent.models import ClaudeBindingSource, CodexTurnSource
from trowel_py.statistics.window import parse_statistics_window

FIXTURES = Path(__file__).parents[1] / "fixtures"


def test_claude_usage_deduplicates_repeated_assistant_message() -> None:
    transcript = FIXTURES / "cc-binding-2.1.197.jsonl"
    source = ClaudeBindingSource(
        session_id="trowel-cc",
        native_session_id="native-redacted",
        transcript_path=transcript,
        start_offset=0,
        end_offset=transcript.stat().st_size,
        bound_at="2026-07-14T18:45:31.582+08:00",
        completed_at="2026-07-14T18:48:53.012+08:00",
        status="completed",
        model=None,
    )
    window = parse_statistics_window(
        datetime.fromisoformat("2026-07-14").date(),
        datetime.fromisoformat("2026-07-14").date(),
        "Asia/Shanghai",
    )

    observation = analyze_claude_binding(source, window)

    assert observation is not None
    assert observation.tokens.input == 17_988
    assert observation.tokens.cache_read == 79_232
    assert observation.tokens.output == 3_844
    assert observation.tokens.total == 101_064
    assert observation.response_samples == (201_430,)
    assert observation.models == ("glm-5.2",)


def test_claude_synthetic_assistant_is_not_a_model_or_first_response(
    tmp_path: Path,
) -> None:
    """Claude Code 内部 synthetic 记录不能冒充真实模型或首响。"""

    transcript = tmp_path / "synthetic.jsonl"
    events = [
        {
            "type": "user",
            "timestamp": "2026-08-03T00:00:00Z",
            "message": {"content": "hello"},
        },
        {
            "type": "assistant",
            "timestamp": "2026-08-03T00:00:01Z",
            "message": {
                "id": "synthetic",
                "model": "<synthetic>",
                "content": [{"type": "text", "text": "internal"}],
                "usage": _usage(),
            },
        },
        {
            "type": "assistant",
            "timestamp": "2026-08-03T00:00:03Z",
            "message": {
                "id": "real",
                "model": "glm-5.2",
                "content": [{"type": "text", "text": "visible"}],
                "usage": _usage(),
            },
        },
    ]
    transcript.write_text(
        "".join(json.dumps(event) + "\n" for event in events),
        encoding="utf-8",
    )
    source = ClaudeBindingSource(
        session_id="trowel-synthetic",
        native_session_id="native-redacted",
        transcript_path=transcript,
        start_offset=0,
        end_offset=transcript.stat().st_size,
        bound_at="2026-08-03T00:00:00+00:00",
        completed_at="2026-08-03T00:00:03+00:00",
        status="completed",
        model=None,
    )
    window = parse_statistics_window(
        datetime(2026, 8, 3, tzinfo=UTC).date(),
        datetime(2026, 8, 3, tzinfo=UTC).date(),
        "UTC",
    )

    observation = analyze_claude_binding(source, window)

    assert observation is not None
    assert observation.models == ("glm-5.2",)
    assert observation.response_samples == (3_000,)
    assert observation.tokens.total == 2


def test_codex_usage_uses_cumulative_watermark_not_final_last() -> None:
    source = CodexTurnSource(
        session_id="trowel-codex",
        native_session_id="thread-redacted",
        turn_id="turn-redacted",
        journal_path=FIXTURES / "codex-turn-0.144.0.jsonl",
        registered_at="2026-07-24T18:48:37.676812+08:00",
        completed_at="2026-07-24T18:50:01.339278+08:00",
        status="completed",
        model="gpt-5.6-sol",
    )
    window = parse_statistics_window(
        datetime.fromisoformat("2026-07-24").date(),
        datetime.fromisoformat("2026-07-24").date(),
        "Asia/Shanghai",
    )

    observation = analyze_codex_session([source], window)

    assert observation is not None
    assert observation.tokens.total == 242_839
    assert observation.tokens.total != 52_735
    assert observation.tokens.input == 239_727
    assert observation.tokens.cache_read == 193_024
    assert observation.tokens.output == 3_112
    assert observation.tokens.reasoning == 773
    assert observation.response_samples == (4_437,)
    assert observation.status == "completed"


def test_batch_adapters_match_individual_windows() -> None:
    """批量入口只减少文件解析次数，不能改变任何单窗统计口径。"""

    claude_path = FIXTURES / "cc-binding-2.1.197.jsonl"
    claude_source = ClaudeBindingSource(
        session_id="trowel-cc",
        native_session_id="native-redacted",
        transcript_path=claude_path,
        start_offset=0,
        end_offset=claude_path.stat().st_size,
        bound_at="2026-07-14T18:45:31.582+08:00",
        completed_at="2026-07-14T18:48:53.012+08:00",
        status="completed",
        model=None,
    )
    codex_source = CodexTurnSource(
        session_id="trowel-codex",
        native_session_id="thread-redacted",
        turn_id="turn-redacted",
        journal_path=FIXTURES / "codex-turn-0.144.0.jsonl",
        registered_at="2026-07-24T18:48:37.676812+08:00",
        completed_at="2026-07-24T18:50:01.339278+08:00",
        status="completed",
        model="gpt-5.6-sol",
    )
    claude_windows = (
        parse_statistics_window(
            datetime.fromisoformat("2026-07-14").date(),
            datetime.fromisoformat("2026-07-14").date(),
            "Asia/Shanghai",
        ),
        parse_statistics_window(
            datetime.fromisoformat("2026-07-15").date(),
            datetime.fromisoformat("2026-07-15").date(),
            "Asia/Shanghai",
        ),
    )
    codex_windows = (
        parse_statistics_window(
            datetime.fromisoformat("2026-07-24").date(),
            datetime.fromisoformat("2026-07-24").date(),
            "Asia/Shanghai",
        ),
        parse_statistics_window(
            datetime.fromisoformat("2026-07-25").date(),
            datetime.fromisoformat("2026-07-25").date(),
            "Asia/Shanghai",
        ),
    )

    claude_batch = analyze_claude_binding_many(claude_source, claude_windows)
    codex_batch = analyze_codex_session_many([codex_source], codex_windows)

    assert claude_batch == tuple(
        analyze_claude_binding(claude_source, window) for window in claude_windows
    )
    assert codex_batch == tuple(
        analyze_codex_session([codex_source], window) for window in codex_windows
    )


def test_codex_adapter_only_decodes_events_used_by_statistics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """大体积正文和工具结果仍贡献活动时间，但不承担 JSON 解码成本。"""

    journal = tmp_path / "large-turn.jsonl"
    relevant = [
        {
            "type": "turn_started",
            "timestamp": "2026-08-03T00:00:00Z",
            "payload": {},
        },
        {
            "type": "assistant_delta",
            "timestamp": "2026-08-03T00:00:01Z",
            "payload": {"delta": "visible"},
        },
        {
            "type": "usage_updated",
            "timestamp": "2026-08-03T00:00:02Z",
            "payload": {
                "total": {"inputTokens": 8, "outputTokens": 2, "totalTokens": 10},
                "last": {"inputTokens": 8, "outputTokens": 2, "totalTokens": 10},
            },
        },
    ]
    irrelevant = [
        {
            "type": "tool_completed",
            "payload": {
                "nested_event": {
                    "type": "usage_updated",
                    "timestamp": "2099-01-01T00:00:00Z",
                },
                "output": "x" * 100_000,
            },
            # normalized writer 总在 payload 之后追加顶层观测时间。
            "timestamp": f"2026-08-03T00:00:{second:02d}Z",
        }
        for second in range(3, 23)
    ]
    journal.write_text(
        "".join(
            json.dumps(event, separators=(",", ":")) + "\n"
            for event in [*relevant, *irrelevant]
        ),
        encoding="utf-8",
    )
    source = CodexTurnSource(
        session_id="trowel-codex",
        native_session_id="thread-redacted",
        turn_id="turn-redacted",
        journal_path=journal,
        registered_at="2026-08-03T00:00:00Z",
        completed_at=None,
        status="running",
        model="gpt-5.6-sol",
    )
    window = parse_statistics_window(
        datetime.fromisoformat("2026-08-03").date(),
        datetime.fromisoformat("2026-08-03").date(),
        "UTC",
    )
    loads_count = 0
    original_loads = json.loads

    def counting_loads(raw: object) -> object:
        """记录 adapter 实际进行的完整 JSON 解码次数。"""

        nonlocal loads_count
        loads_count += 1
        return original_loads(raw)

    monkeypatch.setattr(codex_adapter.json, "loads", counting_loads)

    observation = analyze_codex_session([source], window)

    assert observation is not None
    assert observation.tokens.total == 10
    assert observation.intervals[0].end == datetime(2026, 8, 3, 0, 0, 22, tzinfo=UTC)
    assert loads_count == len(relevant)


def test_claude_models_only_include_events_inside_query_window(tmp_path: Path) -> None:
    """跨日 binding 不把窗外历史模型带进当前日期筛选。"""

    transcript = tmp_path / "cross-day.jsonl"
    events = [
        {
            "type": "user",
            "timestamp": "2026-08-02T23:59:00Z",
            "message": {"content": "first"},
        },
        {
            "type": "assistant",
            "timestamp": "2026-08-02T23:59:30Z",
            "message": {
                "id": "old-model-message",
                "model": "old-model",
                "content": [{"type": "text", "text": "old"}],
                "usage": _usage(),
            },
        },
        {
            "type": "user",
            "timestamp": "2026-08-03T00:01:00Z",
            "message": {"content": "second"},
        },
        {
            "type": "assistant",
            "timestamp": "2026-08-03T00:01:30Z",
            "message": {
                "id": "new-model-message",
                "model": "new-model",
                "content": [{"type": "text", "text": "new"}],
                "usage": _usage(),
            },
        },
    ]
    transcript.write_text(
        "".join(json.dumps(event) + "\n" for event in events),
        encoding="utf-8",
    )
    source = ClaudeBindingSource(
        session_id="trowel-cross-day",
        native_session_id="native-redacted",
        transcript_path=transcript,
        start_offset=0,
        end_offset=transcript.stat().st_size,
        bound_at="2026-08-02T23:59:00+00:00",
        completed_at="2026-08-03T00:01:30+00:00",
        status="completed",
        model=None,
    )
    window = parse_statistics_window(
        datetime(2026, 8, 3, tzinfo=UTC).date(),
        datetime(2026, 8, 3, tzinfo=UTC).date(),
        "UTC",
    )

    observation = analyze_claude_binding(source, window)

    assert observation is not None
    assert observation.models == ("new-model",)
    assert {item.model for item in observation.model_observations} == {"new-model"}


def _usage() -> dict[str, int]:
    """返回字段完整的一条 Claude usage。"""

    return {
        "input_tokens": 1,
        "output_tokens": 1,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }
