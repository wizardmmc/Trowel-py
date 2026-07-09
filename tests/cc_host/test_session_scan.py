"""Tests for session_scan's cc --resume-aligned filtering (slice-026 C3).

A resumable session = UUID filename + non-sidechain first line + has a summary
(customTitle > aiTitle > lastPrompt > firstPrompt). cc --resume hides sidechain
(sub-agent) sessions and metadata-only files; we match that so the history
dropdown total matches what cc would show as resumable. Rules ported from the
leaked source's ``parseSessionInfoFromLite``
(cllaude-code-main/src/utils/listSessionsImpl.ts).
"""
from __future__ import annotations

import json
import os
import time
import uuid as uuidlib
from pathlib import Path

import pytest

from trowel_py.cc_host import session_scan

VALID_UUID = "7dbd6da2-eff1-4af2-b2ab-d476aa48e3be"


class TestWorkdirToSlug:
    """slug rule = CC's sanitizePath: every non-alphanumeric char -> '-'.

    Regression guard: an earlier impl only replaced '/', leaving '_' and '.'
    intact, so workdirs like ``works/telecom_empirical_research`` (underscore)
    produced a slug CC never writes on disk — the history dropdown then
    scanned a non-existent dir and showed 0 sessions.
    """

    def test_slash_becomes_dash(self):
        assert (
            session_scan.workdir_to_slug("/Users/hamxf/workdir")
            == "-Users-hamxf-workdir"
        )

    def test_underscore_becomes_dash(self):
        # real bug: telecom_empirical_research was hidden from history
        slug = session_scan.workdir_to_slug(
            "/Users/hamxf/VirtualVolumn/ClaudeDesktop/works/telecom_empirical_research"
        )
        assert slug == (
            "-Users-hamxf-VirtualVolumn-ClaudeDesktop-works-telecom-empirical-research"
        )

    def test_dot_becomes_dash(self):
        # /Users/hamxf/.claude -> -Users-hamxf--claude
        # (the '.' and the boundary '/' each turn into '-', giving two in a row)
        assert (
            session_scan.workdir_to_slug("/Users/hamxf/.claude")
            == "-Users-hamxf--claude"
        )

    def test_uppercase_and_digits_preserved(self):
        # CC does not lowercase; GraduatePaper and realTimeDect stay, STS2 keeps its digit
        assert session_scan.workdir_to_slug(
            "/Users/hamxf/GraduatePaper/realTimeDect/STS2-Agent"
        ) == "-Users-hamxf-GraduatePaper-realTimeDect-STS2-Agent"

    def test_pathlike_input_matches_str(self):
        # workdir may arrive as os.PathLike (Path); must behave the same as str
        from pathlib import Path

        assert session_scan.workdir_to_slug(Path("/x/y_z")) == (
            session_scan.workdir_to_slug("/x/y_z")
        )

    def test_symlink_resolved_to_match_cc(self, tmp_path: Path):
        """cc sanitizes the realpath (resolves symlinks); trowel must too or
        the slug mismatches cc's on-disk session dir. Real bug: /tmp →
        /private/tmp on macOS, cc wrote -private-tmp-... while trowel looked
        for -tmp-..., so the workflow watcher found nothing. See
        docs/design/front-end/cc-workflow-event-model.md §6."""
        real = tmp_path / "real_dir"
        real.mkdir()
        link = tmp_path / "link_dir"
        link.symlink_to(real)
        # symlink 和其 target 算出同一个 slug(realpath 后一致)
        assert session_scan.workdir_to_slug(str(link)) == (
            session_scan.workdir_to_slug(str(real))
        )


@pytest.fixture()
def fake_projects(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point cc_projects_root at a tmp dir; return the slug dir to write into."""
    root = tmp_path / "projects"
    slug_dir = root / session_scan.workdir_to_slug("/workdir")
    slug_dir.mkdir(parents=True)
    monkeypatch.setattr(session_scan, "cc_projects_root", lambda: root)
    return slug_dir


def _write(path: Path, events: list[dict]) -> None:
    """Write a list of dicts as newline-delimited json."""
    with path.open("w", encoding="utf-8") as fh:
        for ev in events:
            fh.write(json.dumps(ev, ensure_ascii=False) + "\n")


def _user(text: str, **extra: object) -> dict:
    base: dict = {
        "type": "user",
        "isSidechain": False,
        "message": {"role": "user", "content": text},
        "timestamp": "2026-07-04T00:00:00Z",
    }
    base.update(extra)
    return base


def _ai_title(title: str) -> dict:
    return {"type": "ai-title", "aiTitle": title, "timestamp": "2026-07-04T00:00:01Z"}


def _filler(n: int) -> list[dict]:
    """Bulk metadata entries to push later content past the head-read window."""
    return [
        {"type": "attachment", "uuid": f"att-{i}", "timestamp": "2026-07-04T00:00:00Z"}
        for i in range(n)
    ]


# ── filtering rules ────────────────────────────────────────────────────────


def test_excludes_non_uuid_filename(fake_projects: Path) -> None:
    """A non-UUID stem is not a session file cc --resume would list."""
    _write(fake_projects / "not-a-uuid.jsonl", [_user("hi")])
    assert session_scan.list_sessions("/workdir") == []
    assert session_scan.count_sessions("/workdir") == 0


def test_excludes_sidechain_first_line(fake_projects: Path) -> None:
    """First line carrying isSidechain:true marks a sub-agent session → hidden."""
    _write(
        fake_projects / f"{VALID_UUID}.jsonl",
        [
            {
                "type": "user",
                "isSidechain": True,
                "message": {"role": "user", "content": "subagent msg"},
                "timestamp": "2026-07-04T00:00:00Z",
            }
        ],
    )
    assert session_scan.list_sessions("/workdir") == []
    assert session_scan.count_sessions("/workdir") == 0


def test_excludes_metadata_only(fake_projects: Path) -> None:
    """No extractable summary (no title/prompt) → metadata-only → hidden."""
    _write(
        fake_projects / f"{VALID_UUID}.jsonl",
        [
            {"type": "queue-operation", "operation": "enqueue", "timestamp": "2026-07-04T00:00:00Z"},
            {"type": "attachment", "uuid": "a1", "timestamp": "2026-07-04T00:00:00Z"},
        ],
    )
    assert session_scan.list_sessions("/workdir") == []
    assert session_scan.count_sessions("/workdir") == 0


def test_count_filters_all_three_rules(fake_projects: Path) -> None:
    """count_sessions returns the filtered total, not the raw glob count."""
    _write(fake_projects / f"{VALID_UUID}.jsonl", [_user("valid one")])
    _write(fake_projects / f"{uuidlib.uuid4()}.jsonl", [_user("valid two")])
    _write(fake_projects / "not-uuid.jsonl", [_user("bad name")])
    _write(
        fake_projects / f"{uuidlib.uuid4()}.jsonl",
        [{"type": "user", "isSidechain": True, "message": {"role": "user", "content": "sub"}}],
    )
    _write(fake_projects / f"{uuidlib.uuid4()}.jsonl", [{"type": "queue-operation"}])
    assert session_scan.count_sessions("/workdir") == 2
    assert len(session_scan.list_sessions("/workdir")) == 2


# ── title priority ─────────────────────────────────────────────────────────


def test_title_from_first_prompt(fake_projects: Path) -> None:
    """No ai/custom title → title falls back to the first user message text."""
    _write(fake_projects / f"{VALID_UUID}.jsonl", [_user("帮我重构 translator")])
    s = session_scan.list_sessions("/workdir")
    assert len(s) == 1
    assert s[0].title == "帮我重构 translator"


def test_title_prefers_aititle_over_first_prompt(fake_projects: Path) -> None:
    _write(
        fake_projects / f"{VALID_UUID}.jsonl",
        [_user("原始很长的首条用户消息原文"), _ai_title("重构 translator")],
    )
    s = session_scan.list_sessions("/workdir")
    assert len(s) == 1
    assert s[0].title == "重构 translator"


def test_title_prefers_customtitle_over_aititle(fake_projects: Path) -> None:
    _write(
        fake_projects / f"{VALID_UUID}.jsonl",
        [
            _user("原文"),
            _ai_title("ai 起的标题"),
            {"type": "user-custom-title", "customTitle": "用户改的标题", "timestamp": "2026-07-04T00:00:02Z"},
        ],
    )
    s = session_scan.list_sessions("/workdir")
    assert s[0].title == "用户改的标题"


def test_aititle_in_tail_beyond_head_window(fake_projects: Path) -> None:
    """aiTitle only appears near EOF — tail read must find it (head alone misses)."""
    _write(
        fake_projects / f"{VALID_UUID}.jsonl",
        [_user("首条"), *_filler(1000), _ai_title("尾巴里的标题")],
    )
    s = session_scan.list_sessions("/workdir")
    assert len(s) == 1
    assert s[0].title == "尾巴里的标题"


# ── ordering ───────────────────────────────────────────────────────────────


def test_sort_most_recent_first(fake_projects: Path) -> None:
    p_old = fake_projects / f"{VALID_UUID}.jsonl"
    p_new = fake_projects / f"{uuidlib.uuid4()}.jsonl"
    _write(p_old, [_user("old")])
    _write(p_new, [_user("new")])
    later = time.time() + 100
    os.utime(p_new, (later, later))
    titles = [s.title for s in session_scan.list_sessions("/workdir")]
    assert titles == ["new", "old"]


def test_empty_when_projects_dir_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(session_scan, "cc_projects_root", lambda: tmp_path / "nope")
    assert session_scan.list_sessions("/workdir") == []
    assert session_scan.count_sessions("/workdir") == 0


def test_tool_result_echo_not_used_as_title(fake_projects: Path) -> None:
    """A type:user envelope wrapping a tool_result is a tool echo, not a real
    prompt — the title must come from the next real user text (cc's
    extractFirstPromptFromHead filters these)."""
    _write(
        fake_projects / f"{VALID_UUID}.jsonl",
        [
            {
                "type": "user",
                "isSidechain": False,
                "message": {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "tu_1", "content": "tool output"}
                    ],
                },
                "timestamp": "2026-07-04T00:00:00Z",
            },
            _user("真正的首条用户消息"),
        ],
    )
    s = session_scan.list_sessions("/workdir")
    assert len(s) == 1
    assert s[0].title == "真正的首条用户消息"


def test_malformed_file_does_not_crash_list(
    fake_projects: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One bad jsonl must not take down the whole dropdown list."""
    # one good file
    _write(fake_projects / f"{VALID_UUID}.jsonl", [_user("good")])

    def boom(path):  # type: ignore[no-untyped-def]
        if path.stem == VALID_UUID:
            return "good"
        raise ValueError("boom")

    # point a second UUID-stem file at a path whose _extract_title raises
    import uuid as uuidlib

    other = fake_projects / f"{uuidlib.uuid4()}.jsonl"
    other.write_text("garbage\n")
    monkeypatch.setattr(session_scan, "_extract_title", boom)
    # list_sessions swallows the ValueError and returns the surviving titles
    sessions = session_scan.list_sessions("/workdir")
    assert any(s.title == "good" for s in sessions)
