from __future__ import annotations

import json
from pathlib import Path

from trowel_py.cc_host import history
from trowel_py.schemas.cc_host import UserEvent
from tests.cc_host.history._support import (
    _ts_entry,
    _write_jsonl,
)

# 中断会话可能没有 result 行，历史总用时用用户输入到末个 assistant 的时间差近似。


def test_parse_history_turn_duration_single_turn(fake_projects: Path) -> None:
    _write_jsonl(
        fake_projects / "dur.jsonl",
        [
            _ts_entry("user", "2026-07-06T12:00:00.000Z", "hi"),
            _ts_entry(
                "assistant",
                "2026-07-06T12:00:12.000Z",
                [{"type": "text", "text": "hello"}],
            ),
        ],
    )
    events = history.parse_history("/workdir", "dur")
    user_ev = next(e for e in events if isinstance(e, UserEvent))
    assert user_ev.duration_seconds == 12


def test_parse_history_turn_duration_multi_turn(fake_projects: Path) -> None:
    _write_jsonl(
        fake_projects / "dur.jsonl",
        [
            _ts_entry("user", "2026-07-06T12:00:00.000Z", "q1"),
            _ts_entry(
                "assistant",
                "2026-07-06T12:00:10.000Z",
                [{"type": "text", "text": "a1"}],
            ),
            _ts_entry("user", "2026-07-06T12:05:00.000Z", "q2"),
            _ts_entry(
                "assistant",
                "2026-07-06T12:05:33.000Z",
                [{"type": "text", "text": "a2"}],
            ),
        ],
    )
    user_evs = [
        e for e in history.parse_history("/workdir", "dur") if isinstance(e, UserEvent)
    ]
    assert [e.text for e in user_evs] == ["q1", "q2"]
    assert user_evs[0].duration_seconds == 10
    assert user_evs[1].duration_seconds == 33


def test_parse_history_turn_duration_uses_last_assistant_ts(
    fake_projects: Path,
) -> None:
    _write_jsonl(
        fake_projects / "dur.jsonl",
        [
            _ts_entry("user", "2026-07-06T12:00:00.000Z", "hi"),
            _ts_entry(
                "assistant",
                "2026-07-06T12:00:05.000Z",
                [{"type": "text", "text": "part1"}],
            ),
            _ts_entry(
                "assistant",
                "2026-07-06T12:00:20.000Z",
                [{"type": "text", "text": "part2"}],
            ),
        ],
    )
    events = history.parse_history("/workdir", "dur")
    user_ev = next(e for e in events if isinstance(e, UserEvent))
    assert user_ev.duration_seconds == 20


def test_parse_history_turn_duration_missing_user_ts_is_none(
    fake_projects: Path,
) -> None:
    _write_jsonl(
        fake_projects / "dur.jsonl",
        [
            {"type": "user", "message": {"role": "user", "content": "hi"}},
            _ts_entry(
                "assistant",
                "2026-07-06T12:00:10.000Z",
                [{"type": "text", "text": "x"}],
            ),
        ],
    )
    events = history.parse_history("/workdir", "dur")
    user_ev = next(e for e in events if isinstance(e, UserEvent))
    assert user_ev.duration_seconds is None


def test_parse_history_turn_duration_only_user_no_assistant_is_none(
    fake_projects: Path,
) -> None:
    _write_jsonl(
        fake_projects / "dur.jsonl",
        [_ts_entry("user", "2026-07-06T12:00:00.000Z", "hi")],
    )
    events = history.parse_history("/workdir", "dur")
    user_ev = next(e for e in events if isinstance(e, UserEvent))
    assert user_ev.duration_seconds is None


def test_parse_history_turn_duration_tool_result_echo_does_not_split_turn(
    fake_projects: Path,
) -> None:
    _write_jsonl(
        fake_projects / "dur.jsonl",
        [
            _ts_entry("user", "2026-07-06T12:00:00.000Z", "do it"),
            _ts_entry(
                "assistant",
                "2026-07-06T12:00:03.000Z",
                [
                    {
                        "type": "tool_use",
                        "id": "c1",
                        "name": "Bash",
                        "input": {"command": "echo hi"},
                    }
                ],
            ),
            {
                "type": "user",
                "timestamp": "2026-07-06T12:00:08.000Z",
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "c1",
                            "content": "hi",
                        }
                    ],
                },
            },
            _ts_entry(
                "assistant",
                "2026-07-06T12:00:25.000Z",
                [{"type": "text", "text": "done"}],
            ),
        ],
    )
    events = history.parse_history("/workdir", "dur")
    user_evs = [e for e in events if isinstance(e, UserEvent)]
    assert len(user_evs) == 1
    assert user_evs[0].duration_seconds == 25


def test_parse_history_turn_duration_prev_skips_unparseable_line(
    fake_projects: Path,
) -> None:
    target = fake_projects / "dur.jsonl"
    with target.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps(_ts_entry("user", "2026-07-06T12:00:00.000Z", "hi")) + "\n")
        fh.write("not json at all\n")
        fh.write(
            json.dumps(
                _ts_entry(
                    "assistant",
                    "2026-07-06T12:00:20.000Z",
                    [{"type": "text", "text": "x"}],
                )
            )
            + "\n"
        )
    events = history.parse_history("/workdir", "dur")
    user_ev = next(e for e in events if isinstance(e, UserEvent))
    assert user_ev.duration_seconds == 20


def test_parse_history_turn_duration_assistant_missing_ts_is_none(
    fake_projects: Path,
) -> None:
    _write_jsonl(
        fake_projects / "dur.jsonl",
        [
            _ts_entry("user", "2026-07-06T12:00:00.000Z", "hi"),
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "x"}],
                },
            },
        ],
    )
    events = history.parse_history("/workdir", "dur")
    user_ev = next(e for e in events if isinstance(e, UserEvent))
    assert user_ev.duration_seconds is None
