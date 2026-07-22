"""Tests for cc_host.checkpoint — git private-ref snapshots + revert (slice-026 E1).

A checkpoint captures the worktree (tracked + untracked) as a parentless commit
under ``refs/trowel-checkpoints/<turn_id>`` and encodes the CC jsonl cut point
in the commit message. revert restores the worktree from that commit and
truncates the jsonl back to the recorded offset, so a CC turn can be undone.
These tests drive real git in a tmp repo (no network, no real CC).
"""
from __future__ import annotations

import subprocess
import uuid
from pathlib import Path

import pytest

from trowel_py.cc_host import checkpoint
from trowel_py.cc_host.checkpoint import (
    CheckpointMeta,
    NotAGitRepoError,
    UnknownCheckpointError,
)


@pytest.fixture()
def git_repo(tmp_path: Path) -> Path:
    """A tmp git repo with one initial commit so HEAD exists."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
    (repo / "README").write_text("init\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    return repo


def _ref_exists(repo: Path, turn_id: str) -> bool:
    r = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"refs/trowel-checkpoints/{turn_id}"],
        cwd=repo,
        capture_output=True,
    )
    return r.returncode == 0


def _tid() -> str:
    return uuid.uuid4().hex


# ── save ────────────────────────────────────────────────────────────────────


def test_save_creates_ref(git_repo: Path) -> None:
    tid = _tid()
    checkpoint.save(str(git_repo), tid)
    assert _ref_exists(git_repo, tid)


def test_save_does_not_move_HEAD(git_repo: Path) -> None:
    before = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=git_repo, capture_output=True, text=True
    ).stdout.strip()
    checkpoint.save(str(git_repo), _tid())
    after = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=git_repo, capture_output=True, text=True
    ).stdout.strip()
    assert before == after


def test_save_does_not_touch_worktree_or_status(git_repo: Path) -> None:
    checkpoint.save(str(git_repo), _tid())
    assert (git_repo / "README").read_text() == "init\n"
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=git_repo, capture_output=True, text=True
    ).stdout
    assert status == ""


def test_save_message_encodes_meta(git_repo: Path, tmp_path: Path) -> None:
    jsonl = tmp_path / "sess.jsonl"
    jsonl.write_text("a\n")
    tid = _tid()
    checkpoint.save(str(git_repo), tid, cc_session_jsonl_path=str(jsonl), jsonl_offset=2)
    metas = checkpoint.list_checkpoints(str(git_repo))
    m = next(x for x in metas if x.turn_id == tid)
    assert m.cc_session_id == "sess"  # only the stem is kept, not the abs path
    assert m.jsonl_offset == 2
    # privacy (slice-093-pre P3): the absolute jsonl path must NOT be in the
    # commit message — only the session id stem is.
    body = subprocess.run(
        ["git", "-C", str(git_repo), "log", "-1", "--format=%B",
         f"refs/trowel-checkpoints/{tid}"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert str(jsonl) not in body
    assert "/Users/" not in body


# ── revert ──────────────────────────────────────────────────────────────────


def test_revert_restores_modified_file(git_repo: Path) -> None:
    tid = _tid()
    checkpoint.save(str(git_repo), tid)
    (git_repo / "README").write_text("changed by agent\n")
    checkpoint.revert(str(git_repo), tid)
    assert (git_repo / "README").read_text() == "init\n"


def test_revert_restores_deleted_file(git_repo: Path) -> None:
    (git_repo / "tracked").write_text("data\n")
    subprocess.run(["git", "add", "-A"], cwd=git_repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "add tracked"], cwd=git_repo, check=True)
    tid = _tid()
    checkpoint.save(str(git_repo), tid)
    (git_repo / "tracked").unlink()
    checkpoint.revert(str(git_repo), tid)
    assert (git_repo / "tracked").read_text() == "data\n"


def test_revert_removes_untracked_file_created_after_snapshot(git_repo: Path) -> None:
    tid = _tid()
    checkpoint.save(str(git_repo), tid)
    (git_repo / "newfile").write_text("agent created\n")
    checkpoint.revert(str(git_repo), tid)
    assert not (git_repo / "newfile").exists()


def test_revert_truncates_jsonl_to_offset(
    git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    jsonl = tmp_path / "sess.jsonl"
    jsonl.write_text("line1\nline2\n")  # complete entries before the turn
    offset = jsonl.stat().st_size
    tid = _tid()
    # revert re-derives the jsonl path from (workdir, session id); point it at
    # the test file so truncation is observable without a real CC projects dir.
    monkeypatch.setattr(
        checkpoint, "_derive_jsonl_path", lambda wd, sid: jsonl if sid else None
    )
    checkpoint.save(str(git_repo), tid, cc_session_jsonl_path=str(jsonl), jsonl_offset=offset)
    with jsonl.open("a") as fh:  # the turn appends more
        fh.write("line3\nline4\n")
    checkpoint.revert(str(git_repo), tid)
    assert jsonl.read_text() == "line1\nline2\n"


def test_revert_unknown_turn_raises(git_repo: Path) -> None:
    with pytest.raises(UnknownCheckpointError):
        checkpoint.revert(str(git_repo), "nope")


def test_revert_does_not_touch_other_checkpoints(git_repo: Path) -> None:
    keep_tid = _tid()
    drop_tid = _tid()
    checkpoint.save(str(git_repo), keep_tid)
    checkpoint.save(str(git_repo), drop_tid)
    checkpoint.revert(str(git_repo), drop_tid)
    assert _ref_exists(git_repo, keep_tid)


# ── list_checkpoints (persistence) ─────────────────────────────────────────


def test_list_checkpoints_roundtrip(git_repo: Path) -> None:
    tids = [_tid() for _ in range(3)]
    for t in tids:
        checkpoint.save(str(git_repo), t)
    listed = checkpoint.list_checkpoints(str(git_repo))
    assert {m.turn_id for m in listed} == set(tids)


def test_list_checkpoints_empty_when_no_refs(git_repo: Path) -> None:
    assert checkpoint.list_checkpoints(str(git_repo)) == []


# ── gc ──────────────────────────────────────────────────────────────────────


def test_gc_keeps_n_newest(git_repo: Path) -> None:
    for _ in range(60):
        checkpoint.save(str(git_repo), _tid())
    deleted = checkpoint.gc(str(git_repo), keep=50)
    assert deleted == 10
    assert len(checkpoint.list_checkpoints(str(git_repo))) == 50


def test_gc_noop_when_under_keep(git_repo: Path) -> None:
    for _ in range(3):
        checkpoint.save(str(git_repo), _tid())
    assert checkpoint.gc(str(git_repo), keep=50) == 0
    assert len(checkpoint.list_checkpoints(str(git_repo))) == 3


# ── non-git fallback ───────────────────────────────────────────────────────


def test_save_in_non_git_repo_raises(tmp_path: Path) -> None:
    norepo = tmp_path / "norepo"
    norepo.mkdir()
    with pytest.raises(NotAGitRepoError):
        checkpoint.save(str(norepo), _tid())


def test_is_git_repo(tmp_path: Path) -> None:
    norepo = tmp_path / "norepo"
    norepo.mkdir()
    assert checkpoint.is_git_repo(str(norepo)) is False
    subprocess.run(["git", "init", "-q"], cwd=norepo, check=True)
    assert checkpoint.is_git_repo(str(norepo)) is True


# ── message encode/decode ──────────────────────────────────────────────────


def test_message_roundtrip_all_fields() -> None:
    m = CheckpointMeta(
        turn_id="abc",
        cc_session_id="sess",
        jsonl_offset=2048,
        created_at="2026-07-04T12:00:00.000000+00:00",
    )
    body = checkpoint._encode_message(m)  # noqa: SLF001 — exercised in tests
    parsed = checkpoint._decode_message(body, turn_id="abc")
    assert parsed == m


def test_message_roundtrip_minimal_no_session() -> None:
    m = CheckpointMeta(
        turn_id="xyz",
        cc_session_id=None,
        jsonl_offset=None,
        created_at="2026-07-04T12:00:00.000000+00:00",
    )
    parsed = checkpoint._decode_message(checkpoint._encode_message(m), turn_id="xyz")  # noqa: SLF001
    assert parsed == m


def test_truncate_file_scans_back_to_newline_on_partial_line(tmp_path: Path) -> None:
    """If the recorded offset is mid-line (partial-write race), revert scans
    back to the last newline so cc never reads a half-line on --resume."""
    from trowel_py.cc_host import checkpoint as ckpt

    jsonl = tmp_path / "s.jsonl"
    # bytes: a b c \n d e f \n m o r e \n  (offset 6 lands inside "def")
    jsonl.write_bytes(b"abc\ndef\nmore\n")
    # offset 6 → byte at 5 is 'e' (not '\n'); scan back to the \n at index 3
    ckpt._truncate_file(str(jsonl), 6)  # noqa: SLF001
    assert jsonl.read_bytes() == b"abc\n"


def test_truncate_file_noop_when_offset_past_eof(tmp_path: Path) -> None:
    from trowel_py.cc_host import checkpoint as ckpt

    jsonl = tmp_path / "s.jsonl"
    jsonl.write_bytes(b"abc\n")
    ckpt._truncate_file(str(jsonl), 100)  # noqa: SLF001
    assert jsonl.read_bytes() == b"abc\n"
