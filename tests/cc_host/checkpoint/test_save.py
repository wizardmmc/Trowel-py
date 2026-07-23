import subprocess
from pathlib import Path

import pytest

from trowel_py.cc_host import checkpoint
from trowel_py.cc_host.checkpoint import NotAGitRepoError

from .support import checkpoint_ref_exists, git_output, new_turn_id


def test_save_creates_ref(git_repo: Path) -> None:
    turn_id = new_turn_id()

    checkpoint.save(str(git_repo), turn_id)

    assert checkpoint_ref_exists(git_repo, turn_id)


def test_save_does_not_move_head(git_repo: Path) -> None:
    head_before = git_output(git_repo, "rev-parse", "HEAD")

    checkpoint.save(str(git_repo), new_turn_id())

    assert git_output(git_repo, "rev-parse", "HEAD") == head_before


def test_save_does_not_touch_worktree_or_status(git_repo: Path) -> None:
    checkpoint.save(str(git_repo), new_turn_id())

    assert (git_repo / "README").read_text() == "init\n"
    assert git_output(git_repo, "status", "--porcelain") == ""


def test_save_message_keeps_session_id_without_absolute_path(
    git_repo: Path,
    tmp_path: Path,
) -> None:
    transcript = tmp_path / "sess.jsonl"
    transcript.write_text("a\n")
    turn_id = new_turn_id()

    checkpoint.save(
        str(git_repo),
        turn_id,
        cc_session_jsonl_path=str(transcript),
        jsonl_offset=2,
    )
    meta = next(
        item
        for item in checkpoint.list_checkpoints(str(git_repo))
        if item.turn_id == turn_id
    )
    message = git_output(
        git_repo,
        "log",
        "-1",
        "--format=%B",
        f"refs/trowel-checkpoints/{turn_id}",
    )

    assert meta.cc_session_id == "sess"
    assert meta.jsonl_offset == 2
    assert str(transcript) not in message
    assert "/Users/" not in message


def test_save_disabled_returns_meta_without_creating_ref(
    git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TROWEL_CHECKPOINT_ENABLE")
    turn_id = new_turn_id()

    meta = checkpoint.save(str(git_repo), turn_id)

    assert meta.turn_id == turn_id
    assert not checkpoint_ref_exists(git_repo, turn_id)


def test_save_captures_nonignored_untracked_file(git_repo: Path) -> None:
    untracked = git_repo / "draft.txt"
    untracked.write_text("before\n")
    turn_id = new_turn_id()

    checkpoint.save(str(git_repo), turn_id)
    untracked.write_text("after\n")
    checkpoint.revert(str(git_repo), turn_id)

    assert untracked.read_text() == "before\n"


def test_save_excludes_ignored_untracked_file(git_repo: Path) -> None:
    (git_repo / ".gitignore").write_text("*.local\n")
    subprocess.run(["git", "add", ".gitignore"], cwd=git_repo, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "ignore local"],
        cwd=git_repo,
        check=True,
    )
    ignored = git_repo / "secret.local"
    ignored.write_text("before\n")
    turn_id = new_turn_id()

    checkpoint.save(str(git_repo), turn_id)
    ignored.write_text("after\n")
    checkpoint.revert(str(git_repo), turn_id)
    snapshot_paths = git_output(
        git_repo,
        "ls-tree",
        "-r",
        "--name-only",
        f"refs/trowel-checkpoints/{turn_id}",
    ).splitlines()

    assert "secret.local" not in snapshot_paths
    assert ignored.read_text() == "after\n"


def test_save_in_non_git_repo_raises(tmp_path: Path) -> None:
    non_repo = tmp_path / "non-repo"
    non_repo.mkdir()

    with pytest.raises(NotAGitRepoError):
        checkpoint.save(str(non_repo), new_turn_id())


def test_is_git_repo(tmp_path: Path) -> None:
    non_repo = tmp_path / "non-repo"
    non_repo.mkdir()
    assert checkpoint.is_git_repo(str(non_repo)) is False

    subprocess.run(["git", "init", "-q"], cwd=non_repo, check=True)

    assert checkpoint.is_git_repo(str(non_repo)) is True
