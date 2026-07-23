from __future__ import annotations

import json
from pathlib import Path

from trowel_py.cc_host import history
from trowel_py.schemas.cc_host import ThinkingEvent
from tests.cc_host.history._support import (
    _ts_entry,
    _write_jsonl,
)

# JSONL 不记录实时 heartbeat，历史思考时长只能由相邻有效事件的时间差近似。


def test_compute_thinking_duration_normal() -> None:
    assert (
        history._compute_thinking_duration(
            "2026-07-06T12:38:00.000Z", "2026-07-06T12:38:23.000Z"
        )
        == 23
    )


def test_compute_thinking_duration_no_prev() -> None:
    assert history._compute_thinking_duration(None, "2026-07-06T12:38:23.000Z") is None


def test_compute_thinking_duration_no_cur() -> None:
    assert history._compute_thinking_duration("2026-07-06T12:38:00.000Z", None) is None


def test_compute_thinking_duration_zero_is_none() -> None:
    assert (
        history._compute_thinking_duration(
            "2026-07-06T12:38:00.000Z", "2026-07-06T12:38:00.000Z"
        )
        is None
    )


def test_compute_thinking_duration_negative_is_none() -> None:
    assert (
        history._compute_thinking_duration(
            "2026-07-06T12:38:30.000Z", "2026-07-06T12:38:00.000Z"
        )
        is None
    )


def test_compute_thinking_duration_clamps_to_one() -> None:
    assert (
        history._compute_thinking_duration(
            "2026-07-06T12:38:00.000Z", "2026-07-06T12:38:00.600Z"
        )
        == 1
    )


def test_compute_thinking_duration_unparseable_is_none() -> None:
    assert history._compute_thinking_duration("garbage", "2026-07-06T12:38:00Z") is None
    assert history._compute_thinking_duration(None, None) is None


def test_parse_history_thinking_duration_from_prev_entry(fake_projects: Path) -> None:
    _write_jsonl(
        fake_projects / "abc.jsonl",
        [
            _ts_entry("user", "2026-07-06T12:38:00.000Z", "hi"),
            _ts_entry(
                "assistant",
                "2026-07-06T12:38:23.000Z",
                [{"type": "thinking", "thinking": "reasoning"}],
            ),
        ],
    )
    events = history.parse_history("/workdir", "abc")
    thinking = next(e for e in events if isinstance(e, ThinkingEvent))
    assert thinking.thinking_duration_seconds == 23


def test_parse_history_thinking_first_entry_has_no_duration(
    fake_projects: Path,
) -> None:
    _write_jsonl(
        fake_projects / "abc.jsonl",
        [
            _ts_entry(
                "assistant",
                "2026-07-06T12:38:23.000Z",
                [{"type": "thinking", "thinking": "reasoning"}],
            ),
        ],
    )
    events = history.parse_history("/workdir", "abc")
    thinking = next(e for e in events if isinstance(e, ThinkingEvent))
    assert thinking.thinking_duration_seconds is None


def test_parse_history_consecutive_thinkings_each_stamped(
    fake_projects: Path,
) -> None:
    _write_jsonl(
        fake_projects / "abc.jsonl",
        [
            _ts_entry("user", "2026-07-06T12:00:00.000Z", "hi"),
            _ts_entry(
                "assistant",
                "2026-07-06T12:00:10.000Z",
                [{"type": "thinking", "thinking": "a"}],
            ),
            _ts_entry(
                "assistant",
                "2026-07-06T12:00:15.000Z",
                [{"type": "thinking", "thinking": "b"}],
            ),
        ],
    )
    thinkings = [
        e
        for e in history.parse_history("/workdir", "abc")
        if isinstance(e, ThinkingEvent)
    ]
    assert thinkings[0].thinking_duration_seconds == 10
    assert thinkings[1].thinking_duration_seconds == 5


def test_parse_history_thinking_missing_timestamp_no_duration(
    fake_projects: Path,
) -> None:
    _write_jsonl(
        fake_projects / "abc.jsonl",
        [
            {"type": "user", "message": {"role": "user", "content": "hi"}},
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "thinking", "thinking": "x"}],
                },
            },
        ],
    )
    events = history.parse_history("/workdir", "abc")
    thinking = next(e for e in events if isinstance(e, ThinkingEvent))
    assert thinking.thinking_duration_seconds is None


def test_parse_history_thinking_prev_skips_unparseable_line(
    fake_projects: Path,
) -> None:
    target = fake_projects / "abc.jsonl"
    with target.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps(_ts_entry("user", "2026-07-06T12:00:00.000Z", "hi")) + "\n")
        fh.write("not json at all\n")
        fh.write(
            json.dumps(
                _ts_entry(
                    "assistant",
                    "2026-07-06T12:00:20.000Z",
                    [{"type": "thinking", "thinking": "x"}],
                )
            )
            + "\n"
        )
    events = history.parse_history("/workdir", "abc")
    thinking = next(e for e in events if isinstance(e, ThinkingEvent))
    assert thinking.thinking_duration_seconds == 20
