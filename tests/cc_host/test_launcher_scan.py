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
        """slice-027: defaults follow cc settings.json — launcher does NOT pass
        --model/--fallback-model/--effort unless explicitly given. The old
        glm-5.2 / glm-5.1 / medium were placeholders; trowel now defers to cc's
        own config resolution (ANTHROPIC_MODEL env > settings.model)."""
        args = build_args(workdir=tmp_path)
        assert "-p" in args
        assert "--input-format" in args and "stream-json" in args
        assert "--output-format" in args and "stream-json" in args
        assert "--verbose" in args
        assert "--model" not in args
        assert "--fallback-model" not in args
        assert "--effort" not in args
        assert "--permission-mode" in args and "bypassPermissions" in args

    def test_fallback_model_override(self, tmp_path: Path):
        args = build_args(workdir=tmp_path, fallback_model="sonnet")
        i = args.index("--fallback-model")
        assert args[i + 1] == "sonnet"

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

    def test_kwargs_include_large_readline_limit(self, tmp_path: Path):
        """slice-028 bug1: asyncio subprocess readline 默认 64KB limit 撑爆 turn
        (cc 单行 stream-json > 64KB → ValueError → host_error)。kwargs 必须传
        limit (>= 1MB) 给 create_subprocess_exec，给 tcc 一个大接水桶。"""
        kw = build_subprocess_kwargs(workdir=tmp_path)
        assert "limit" in kw
        assert kw["limit"] >= 1024 * 1024  # 至少 1MB（实测 slice-027 单行 1.08MB）

    def test_env_arg_propagated_to_kwargs(self, tmp_path: Path):
        """slice-030: launcher accepts a pre-built env dict (the proxy delta
        merged into os.environ by CCHost) and passes it as the subprocess env
        so CC routes through the local reverse proxy."""
        kw = build_subprocess_kwargs(workdir=tmp_path, env={"FOO": "bar"})
        assert kw["env"]["FOO"] == "bar"

    def test_env_none_omits_env_key(self, tmp_path: Path):
        """Back-compat: no env= means the subprocess inherits the parent env
        (create_subprocess_exec default). Don't set an env key in that case."""
        kw = build_subprocess_kwargs(workdir=tmp_path)
        assert "env" not in kw


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
        import uuid as uuidlib

        root = tmp_path / "projects"
        sid = str(uuidlib.uuid4())
        self._write_session(root, "-wd", sid, [
            {"type": "system", "subtype": "init"},
            {"type": "user", "isSidechain": False, "message": {"role": "user",
                "content": [{"type": "text", "text": "hello there"}]}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "hi"}]}},
        ])
        monkeypatch.setattr("trowel_py.cc_host.session_scan.cc_projects_root", lambda: root)
        sessions = list_sessions("/wd")
        assert len(sessions) == 1
        s = sessions[0]
        assert isinstance(s, SessionSummary)
        assert s.cc_session_id == sid
        assert s.title == "hello there"

    def test_includes_legacy_sessions_not_created_this_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        # the whole point of GET /sessions: open june's session too
        import uuid as uuidlib

        root = tmp_path / "projects"
        sid = str(uuidlib.uuid4())
        self._write_session(root, "-wd", sid, [
            {"type": "user", "isSidechain": False, "message": {"role": "user",
                "content": [{"type": "text", "text": "old msg"}]}},
        ])
        monkeypatch.setattr("trowel_py.cc_host.session_scan.cc_projects_root", lambda: root)
        sessions = list_sessions("/wd")
        assert sessions[0].cc_session_id == sid

    def test_multiple_sessions_sorted_recent_first(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        import os
        import uuid as uuidlib

        root = tmp_path / "projects"
        old_sid = str(uuidlib.uuid4())
        new_sid = str(uuidlib.uuid4())
        older = self._write_session(root, "-wd", old_sid, [
            {"type": "user", "isSidechain": False, "message": {"role": "user",
                "content": [{"type": "text", "text": "o"}]}}])
        newer = self._write_session(root, "-wd", new_sid, [
            {"type": "user", "isSidechain": False, "message": {"role": "user",
                "content": [{"type": "text", "text": "n"}]}}])
        os.utime(older, (1, 1))
        os.utime(newer, (2, 2))
        monkeypatch.setattr("trowel_py.cc_host.session_scan.cc_projects_root", lambda: root)
        sessions = list_sessions("/wd")
        assert [s.cc_session_id for s in sessions] == [new_sid, old_sid]

    def test_missing_projects_dir_returns_empty(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr("trowel_py.cc_host.session_scan.cc_projects_root",
                            lambda: tmp_path / "nope")
        assert list_sessions("/wd") == []

    def test_metadata_only_session_is_excluded(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        # slice-026 C3: a session with no extractable summary (no user text,
        # no ai/custom title) is hidden by cc --resume, and by us too.
        import uuid as uuidlib

        root = tmp_path / "projects"
        self._write_session(root, "-wd", str(uuidlib.uuid4()), [
            {"type": "system", "subtype": "init"},
        ])
        monkeypatch.setattr("trowel_py.cc_host.session_scan.cc_projects_root", lambda: root)
        assert list_sessions("/wd") == []

    def test_stream_json_session_visible_with_large_metadata_prefix(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """slice-028 bug2: stream-json 模式（tcc 跑的会话）前几行是 queue-operation/
        attachment metadata，单行大。旧 _HEAD_BYTES=8192 到不了第一条 user message
        → title 提取失败 → 漏出历史列表（实测 128a31b0：user 在第 5 行，8KB 只覆盖
        前 4 行）。增大 head 后这种会话要可见。"""
        import uuid as uuidlib

        root = tmp_path / "projects"
        sid = str(uuidlib.uuid4())
        big_meta = '{"type":"attachment","isSidechain":false,"data":"' + "x" * 5000 + '"}'
        d = root / "-wd"
        d.mkdir(parents=True)
        # 前 4 行 metadata ~12KB > 8KB head（旧值）；第 5 行才是 user message
        (d / f"{sid}.jsonl").write_text(
            json.dumps({"type": "queue-operation", "data": "x" * 500}) + "\n"
            + json.dumps({"type": "queue-operation", "data": "x" * 500}) + "\n"
            + big_meta + "\n" + big_meta + "\n"
            + json.dumps({"type": "user", "isSidechain": False, "message": {"role": "user",
                "content": [{"type": "text", "text": "slice-028 grill 准备实现"}]}}) + "\n"
        )
        monkeypatch.setattr("trowel_py.cc_host.session_scan.cc_projects_root", lambda: root)
        sessions = list_sessions("/wd")
        assert any(s.cc_session_id == sid for s in sessions)
        assert sessions[0].title == "slice-028 grill 准备实现"

    def _write_timed_sessions(
        self, root: Path, slug: str, ids_with_mtime: list[tuple[str, int]]
    ) -> None:
        """Write one minimal session per id, each pinned to the given mtime."""
        import os
        for sid, mtime in ids_with_mtime:
            f = self._write_session(root, slug, sid, [
                {"type": "user", "isSidechain": False, "message": {"role": "user",
                    "content": [{"type": "text", "text": sid}]}},
            ])
            os.utime(f, (mtime, mtime))

    def test_limit_caps_to_n_most_recent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        # 5 sessions, mtimes 1..5 — limit=3 must keep the 3 newest (5,4,3)
        import uuid as uuidlib

        root = tmp_path / "projects"
        ids = [str(uuidlib.uuid4()) for _ in range(5)]
        self._write_timed_sessions(root, "-wd", list(zip(ids, [1, 2, 3, 4, 5])))
        monkeypatch.setattr("trowel_py.cc_host.session_scan.cc_projects_root", lambda: root)
        sessions = list_sessions("/wd", limit=3)
        assert [s.cc_session_id for s in sessions] == [ids[4], ids[3], ids[2]]

    def test_limit_none_returns_all(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        # default behavior unchanged: no cap when limit is None
        import uuid as uuidlib

        root = tmp_path / "projects"
        ids = [str(uuidlib.uuid4()) for _ in range(5)]
        self._write_timed_sessions(root, "-wd", list(zip(ids, [1, 2, 3, 4, 5])))
        monkeypatch.setattr("trowel_py.cc_host.session_scan.cc_projects_root", lambda: root)
        sessions = list_sessions("/wd")
        assert len(sessions) == 5

    def test_limit_larger_than_count_returns_all(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        import uuid as uuidlib

        root = tmp_path / "projects"
        ids = [str(uuidlib.uuid4()), str(uuidlib.uuid4())]
        self._write_timed_sessions(root, "-wd", list(zip(ids, [1, 2])))
        monkeypatch.setattr("trowel_py.cc_host.session_scan.cc_projects_root", lambda: root)
        sessions = list_sessions("/wd", limit=10)
        assert len(sessions) == 2


class TestCountSessions:
    def _write_session(self, root: Path, slug: str, sid: str) -> Path:
        d = root / slug
        d.mkdir(parents=True, exist_ok=True)
        f = d / f"{sid}.jsonl"
        f.write_text(json.dumps({"type": "user", "isSidechain": False, "message": {"role": "user",
            "content": [{"type": "text", "text": sid}]}}) + "\n")
        return f

    def test_counts_all_session_files(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        import uuid as uuidlib

        root = tmp_path / "projects"
        for _ in range(3):
            self._write_session(root, "-wd", str(uuidlib.uuid4()))
        monkeypatch.setattr("trowel_py.cc_host.session_scan.cc_projects_root", lambda: root)
        assert count_sessions("/wd") == 3

    def test_count_zero_when_no_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr("trowel_py.cc_host.session_scan.cc_projects_root",
                            lambda: tmp_path / "nope")
        assert count_sessions("/wd") == 0

    def test_count_ignores_non_jsonl_and_metadata_only(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        # slice-026 C3: count is the FILTERED total. A .md file, a non-UUID
        # .jsonl name, and a metadata-only `{}` jsonl are all excluded.
        import uuid as uuidlib

        root = tmp_path / "projects"
        d = root / "-wd"
        d.mkdir(parents=True)
        (d / f"{uuidlib.uuid4()}.jsonl").write_text(
            json.dumps({"type": "user", "isSidechain": False, "message": {"role": "user",
                "content": [{"type": "text", "text": "one"}]}}) + "\n")
        (d / "notes.md").write_text("not a session\n")
        (d / "not-a-uuid.jsonl").write_text(
            json.dumps({"type": "user", "message": {"role": "user",
                "content": [{"type": "text", "text": "two"}]}}) + "\n")
        (d / f"{uuidlib.uuid4()}.jsonl").write_text("{}\n")  # metadata-only
        monkeypatch.setattr("trowel_py.cc_host.session_scan.cc_projects_root", lambda: root)
        assert count_sessions("/wd") == 1
