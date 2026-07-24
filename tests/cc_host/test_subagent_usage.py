from __future__ import annotations

import json
from pathlib import Path

import pytest

from trowel_py.cc_host.subagent_usage import (
    merge_usage,
    subagent_transcript_path,
    sum_transcript_usage,
)


def _write_lines(path: Path, lines: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for entry in lines:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _assistant_msg(*, in_tok: int = 0, out_tok: int = 0, tool_uses: int = 0) -> dict:
    content: list[dict] = [{"type": "text", "text": "thinking..."}]
    for i in range(tool_uses):
        content.append(
            {"type": "tool_use", "id": f"tu_{i}", "name": "Bash", "input": {}}
        )
    return {
        "type": "assistant",
        "isSidechain": True,
        "attributionAgent": "general-purpose",
        "message": {
            "role": "assistant",
            "usage": {"input_tokens": in_tok, "output_tokens": out_tok},
            "content": content,
        },
    }


def _user_result() -> dict:
    return {
        "type": "user",
        "isSidechain": True,
        "message": {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "tu_0", "content": "ok"}
            ],
        },
    }


def test_sum_accumulates_input_plus_output_tokens(tmp_path: Path):
    p = tmp_path / "agent-x.jsonl"
    _write_lines(
        p,
        [
            _assistant_msg(in_tok=100, out_tok=50),
            _user_result(),
            _assistant_msg(in_tok=200, out_tok=30),
        ],
    )
    usage = sum_transcript_usage(p)
    assert usage is not None
    assert usage["total_tokens"] == 380


def test_sum_counts_tool_uses(tmp_path: Path):
    p = tmp_path / "agent-x.jsonl"
    _write_lines(
        p,
        [
            _assistant_msg(tool_uses=2),
            _assistant_msg(tool_uses=1),
        ],
    )
    usage = sum_transcript_usage(p)
    assert usage is not None
    assert usage["tool_uses"] == 3


def test_sum_skips_rows_without_usage(tmp_path: Path):
    p = tmp_path / "agent-x.jsonl"
    _write_lines(
        p,
        [
            {"type": "assistant", "message": {"role": "assistant", "content": []}},
            _assistant_msg(in_tok=10, out_tok=5),
        ],
    )
    usage = sum_transcript_usage(p)
    assert usage is not None
    assert usage["total_tokens"] == 15


def test_sum_zero_usage_rows_do_not_break(tmp_path: Path):
    p = tmp_path / "agent-x.jsonl"
    _write_lines(
        p,
        [
            _assistant_msg(in_tok=0, out_tok=0),
            _assistant_msg(in_tok=500, out_tok=100),
        ],
    )
    usage = sum_transcript_usage(p)
    assert usage is not None
    assert usage["total_tokens"] == 600
    assert usage["tool_uses"] == 0


def test_sum_missing_file_returns_none(tmp_path: Path):
    p = tmp_path / "does-not-exist.jsonl"
    assert sum_transcript_usage(p) is None


def test_sum_empty_file_returns_zero(tmp_path: Path):
    p = tmp_path / "empty.jsonl"
    _write_lines(p, [])
    usage = sum_transcript_usage(p)
    assert usage == {"total_tokens": 0, "tool_uses": 0}


def test_sum_unparseable_lines_skipped(tmp_path: Path):
    p = tmp_path / "agent-x.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(_assistant_msg(in_tok=10, out_tok=5)) + "\n"
        "not json at all\n" + json.dumps(_assistant_msg(in_tok=20, out_tok=5)) + "\n",
        encoding="utf-8",
    )
    usage = sum_transcript_usage(p)
    assert usage is not None
    assert usage["total_tokens"] == 40


def test_subagent_transcript_path_uses_task_id_as_agent_id(tmp_path: Path):
    p = subagent_transcript_path("/workdir", "sess-123", "a53f21d96e2f13c9a")
    assert p.name == "agent-a53f21d96e2f13c9a.jsonl"
    assert p.parent.name == "subagents"
    assert p.parent.parent.name == "sess-123"


def test_merge_usage_transcript_sum_overrides_cc_tokens():
    merged = merge_usage(
        {"total_tokens": 0, "tool_uses": 99, "duration_ms": 4865},
        {"total_tokens": 600, "tool_uses": 3},
    )
    assert merged["total_tokens"] == 600
    assert merged["tool_uses"] == 3


def test_merge_usage_preserves_cc_duration_ms():
    merged = merge_usage(
        {"total_tokens": 0, "duration_ms": 9999},
        {"total_tokens": 100, "tool_uses": 1},
    )
    assert merged["duration_ms"] == 9999
    assert merged["total_tokens"] == 100


def test_merge_usage_handles_none_cc_usage():
    merged = merge_usage(None, {"total_tokens": 50, "tool_uses": 0})
    assert merged == {"total_tokens": 50, "tool_uses": 0}


def test_backfill_integration_reads_transcript_and_overrides_empty_cc_usage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trowel_py.cc_host import service, session_scan, subagent_usage
    from trowel_py.cc_host.service import CCHost
    from trowel_py.schemas.cc_host import SubagentProgressEvent

    # 三个模块都持有 root facade，测试必须一起隔离到 tmp_path。
    monkeypatch.setattr(session_scan, "cc_projects_root", lambda: tmp_path)
    monkeypatch.setattr(service, "cc_projects_root", lambda: tmp_path)
    monkeypatch.setattr(subagent_usage, "cc_projects_root", lambda: tmp_path)

    slug = session_scan.workdir_to_slug("/workdir")
    sub_dir = tmp_path / slug / "sess-1" / "subagents"
    sub_dir.mkdir(parents=True)
    _write_lines(
        sub_dir / "agent-taskA.jsonl",
        [_assistant_msg(in_tok=100, out_tok=50, tool_uses=2)],
    )

    host = CCHost("s-trowel", "/workdir")
    host._cc_session_id = "sess-1"
    tev = SubagentProgressEvent(
        tool_use_id="tu_1",
        task_id="taskA",
        status="completed",
        usage={"total_tokens": 0, "tool_uses": 99, "duration_ms": 4865},
    )

    out = host._backfill_subagent_usage(tev)

    assert out.usage["total_tokens"] == 150
    assert out.usage["tool_uses"] == 2
    assert out.usage["duration_ms"] == 4865


def test_backfill_returns_original_when_no_transcript(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trowel_py.cc_host import service, session_scan, subagent_usage
    from trowel_py.cc_host.service import CCHost
    from trowel_py.schemas.cc_host import SubagentProgressEvent

    monkeypatch.setattr(session_scan, "cc_projects_root", lambda: tmp_path)
    monkeypatch.setattr(service, "cc_projects_root", lambda: tmp_path)
    monkeypatch.setattr(subagent_usage, "cc_projects_root", lambda: tmp_path)

    host = CCHost("s-trowel", "/workdir")
    host._cc_session_id = "sess-1"
    tev = SubagentProgressEvent(
        tool_use_id="tu_1",
        task_id="taskNoTranscript",
        status="completed",
        usage={"total_tokens": 0, "tool_uses": 1},
    )

    out = host._backfill_subagent_usage(tev)
    assert out is tev
