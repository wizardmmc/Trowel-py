"""Tests for cc_host.launcher (CC subprocess args) and cc_host.session_scan
(listing resumable CC history sessions).

Both are pure logic — no real subprocess, no real ~/.claude.
"""
import json
from pathlib import Path

import pytest

from trowel_py.cc_host.launcher import build_args, build_subprocess_kwargs
from trowel_py.cc_host.session_scan import (
    SessionSummary,
    count_sessions,
    list_sessions,
    workdir_to_slug,
)


class TestBuildArgs:
    def test_default_args_shape(self, tmp_path: Path):
        args = build_args(workdir=tmp_path)
        # program + flags present (no real claude binary path check here)
        assert "-p" in args
        assert "--input-format" in args and "stream-json" in args
        assert "--output-format" in args and "stream-json" in args
        assert "--verbose" in args
        assert "--model" in args and "glm-5.2" in args
        assert "--fallback-model" in args and "glm-5.1" in args
        assert "--effort" in args and "medium" in args
        assert "--permission-mode" in args and "bypassPermissions" in args

    def test_effort_override(self, tmp_path: Path):
        args = build_args(workdir=tmp_path, effort="high")
        i = args.index("--effort")
        assert args[i + 1] == "high"

    def test_model_override(self, tmp_path: Path):
        args = build_args(workdir=tmp_path, model="glm-5.1")
        i = args.index("--model")
        assert args[i + 1] == "glm-5.1"

    def test_resume_appends_resume_flag(self, tmp_path: Path):
        args = build_args(workdir=tmp_path, resume_from="abc-123")
        i = args.index("--resume")
        assert args[i + 1] == "abc-123"

    def test_permission_mode_passthrough(self, tmp_path: Path):
        args = build_args(workdir=tmp_path, permission_mode="default")
        i = args.index("--permission-mode")
        assert args[i + 1] == "default"

    def test_workdir_is_not_in_args(self, tmp_path: Path):
        # spec: workdir is the subprocess cwd, NOT --add-dir
        args = build_args(workdir=tmp_path)
        assert "--add-dir" not in args
        assert str(tmp_path) not in args

    def test_default_includes_permission_prompt_tool_stdio(self, tmp_path: Path):
        # slice-025-c: bypass + --permission-prompt-tool stdio is the default
        # route — ordinary tools stay silent, only interactive tools
        # (AskUserQuestion et al.) trigger control_request. Ground truth: 052.
        args = build_args(workdir=tmp_path)
        i = args.index("--permission-prompt-tool")
        assert args[i + 1] == "stdio"

    def test_permission_prompt_tool_can_be_disabled(self, tmp_path: Path):
        args = build_args(workdir=tmp_path, permission_prompt_tool=None)
        assert "--permission-prompt-tool" not in args


class TestSubprocessKwargs:
    def test_cwd_is_workdir_and_new_session(self, tmp_path: Path):
        kw = build_subprocess_kwargs(workdir=tmp_path)
        assert kw["cwd"] == str(tmp_path)
        assert kw["start_new_session"] is True
        assert kw["stdin"] is not None
        assert kw["stdout"] is not None
        assert kw["stderr"] is not None


class TestSlug:
    def test_slug_replaces_slashes_with_dashes(self):
        assert workdir_to_slug("/Users/hamxf/ClaudeDesktop") == \
            "-Users-hamxf-ClaudeDesktop"


class TestListSessions:
    def _write_session(self, root: Path, slug: str, sid: str, lines: list[dict]) -> Path:
        d = root / slug
        d.mkdir(parents=True, exist_ok=True)
        f = d / f"{sid}.jsonl"
        f.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
        return f

    def test_lists_sessions_with_title_from_first_user_message(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        root = tmp_path / "projects"
        self._write_session(root, "-wd", "sess-a", [
            {"type": "system", "subtype": "init"},
            {"type": "user", "message": {"role": "user",
                "content": [{"type": "text", "text": "hello there"}]}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "hi"}]}},
        ])
        monkeypatch.setattr("trowel_py.cc_host.session_scan.cc_projects_root", lambda: root)
        sessions = list_sessions("/wd")
        assert len(sessions) == 1
        s = sessions[0]
        assert isinstance(s, SessionSummary)
        assert s.cc_session_id == "sess-a"
        assert s.title == "hello there"

    def test_includes_legacy_sessions_not_created_this_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        # the whole point of GET /sessions: open june's session too
        root = tmp_path / "projects"
        self._write_session(root, "-wd", "june-session", [
            {"type": "user", "message": {"role": "user",
                "content": [{"type": "text", "text": "old msg"}]}},
        ])
        monkeypatch.setattr("trowel_py.cc_host.session_scan.cc_projects_root", lambda: root)
        sessions = list_sessions("/wd")
        assert sessions[0].cc_session_id == "june-session"

    def test_multiple_sessions_sorted_recent_first(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        root = tmp_path / "projects"
        older = self._write_session(root, "-wd", "old", [
            {"type": "user", "message": {"role": "user",
                "content": [{"type": "text", "text": "o"}]}}])
        newer = self._write_session(root, "-wd", "new", [
            {"type": "user", "message": {"role": "user",
                "content": [{"type": "text", "text": "n"}]}}])
        # new mtime > old mtime
        import os, time
        os.utime(older, (1, 1))
        os.utime(newer, (2, 2))
        monkeypatch.setattr("trowel_py.cc_host.session_scan.cc_projects_root", lambda: root)
        sessions = list_sessions("/wd")
        assert [s.cc_session_id for s in sessions] == ["new", "old"]

    def test_missing_projects_dir_returns_empty(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr("trowel_py.cc_host.session_scan.cc_projects_root",
                            lambda: tmp_path / "nope")
        assert list_sessions("/wd") == []

    def test_title_falls_back_when_no_user_message(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        root = tmp_path / "projects"
        self._write_session(root, "-wd", "s", [
            {"type": "system", "subtype": "init"},
        ])
        monkeypatch.setattr("trowel_py.cc_host.session_scan.cc_projects_root", lambda: root)
        s = list_sessions("/wd")[0]
        assert s.title == ""  # graceful fallback, not a crash

    def _write_timed_sessions(
        self, root: Path, slug: str, ids_with_mtime: list[tuple[str, int]]
    ) -> None:
        """Write one minimal session per id, each pinned to the given mtime."""
        import os
        for sid, mtime in ids_with_mtime:
            f = self._write_session(root, slug, sid, [
                {"type": "user", "message": {"role": "user",
                    "content": [{"type": "text", "text": sid}]}},
            ])
            os.utime(f, (mtime, mtime))

    def test_limit_caps_to_n_most_recent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        # 5 sessions, mtimes 1..5 — limit=3 must keep the 3 newest (5,4,3)
        root = tmp_path / "projects"
        self._write_timed_sessions(root, "-wd", [
            ("s1", 1), ("s2", 2), ("s3", 3), ("s4", 4), ("s5", 5),
        ])
        monkeypatch.setattr("trowel_py.cc_host.session_scan.cc_projects_root", lambda: root)
        sessions = list_sessions("/wd", limit=3)
        assert [s.cc_session_id for s in sessions] == ["s5", "s4", "s3"]

    def test_limit_none_returns_all(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        # default behavior unchanged: no cap when limit is None
        root = tmp_path / "projects"
        self._write_timed_sessions(root, "-wd", [
            ("s1", 1), ("s2", 2), ("s3", 3), ("s4", 4), ("s5", 5),
        ])
        monkeypatch.setattr("trowel_py.cc_host.session_scan.cc_projects_root", lambda: root)
        sessions = list_sessions("/wd")
        assert len(sessions) == 5

    def test_limit_larger_than_count_returns_all(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        root = tmp_path / "projects"
        self._write_timed_sessions(root, "-wd", [("s1", 1), ("s2", 2)])
        monkeypatch.setattr("trowel_py.cc_host.session_scan.cc_projects_root", lambda: root)
        sessions = list_sessions("/wd", limit=10)
        assert len(sessions) == 2


class TestCountSessions:
    def _write_session(self, root: Path, slug: str, sid: str) -> Path:
        d = root / slug
        d.mkdir(parents=True, exist_ok=True)
        f = d / f"{sid}.jsonl"
        f.write_text(json.dumps({"type": "user", "message": {"role": "user",
            "content": [{"type": "text", "text": sid}]}}) + "\n")
        return f

    def test_counts_all_session_files(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        root = tmp_path / "projects"
        for sid in ("a", "b", "c"):
            self._write_session(root, "-wd", sid)
        monkeypatch.setattr("trowel_py.cc_host.session_scan.cc_projects_root", lambda: root)
        assert count_sessions("/wd") == 3

    def test_count_zero_when_no_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr("trowel_py.cc_host.session_scan.cc_projects_root",
                            lambda: tmp_path / "nope")
        assert count_sessions("/wd") == 0

    def test_count_ignores_non_jsonl(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        root = tmp_path / "projects"
        d = root / "-wd"
        d.mkdir(parents=True)
        (d / "s1.jsonl").write_text("{}\n")
        (d / "notes.md").write_text("not a session\n")
        (d / "s2.jsonl").write_text("{}\n")
        monkeypatch.setattr("trowel_py.cc_host.session_scan.cc_projects_root", lambda: root)
        assert count_sessions("/wd") == 2
