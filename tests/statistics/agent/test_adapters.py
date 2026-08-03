"""用 L01 脱敏真实记录验证双 runtime 统计口径。"""

import json
from datetime import UTC, datetime
from pathlib import Path

from trowel_py.statistics.agent.claude import analyze_claude_binding
from trowel_py.statistics.agent.codex import analyze_codex_session
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
