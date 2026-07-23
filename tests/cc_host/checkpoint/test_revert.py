import subprocess
from pathlib import Path

import pytest

from trowel_py.cc_host import checkpoint
from trowel_py.cc_host.checkpoint import UnknownCheckpointError

from .support import checkpoint_ref_exists, git_output, new_turn_id


def test_revert_restores_modified_file(git_repo: Path) -> None:
    turn_id = new_turn_id()
    checkpoint.save(str(git_repo), turn_id)
    (git_repo / "README").write_text("changed by agent\n")

    checkpoint.revert(str(git_repo), turn_id)

    assert (git_repo / "README").read_text() == "init\n"


def test_revert_restores_deleted_file(git_repo: Path) -> None:
    tracked = git_repo / "tracked"
    tracked.write_text("data\n")
    subprocess.run(["git", "add", "-A"], cwd=git_repo, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "add tracked"],
        cwd=git_repo,
        check=True,
    )
    turn_id = new_turn_id()
    checkpoint.save(str(git_repo), turn_id)
    tracked.unlink()

    checkpoint.revert(str(git_repo), turn_id)

    assert tracked.read_text() == "data\n"


def test_revert_removes_untracked_file_created_after_snapshot(
    git_repo: Path,
) -> None:
    turn_id = new_turn_id()
    checkpoint.save(str(git_repo), turn_id)
    created_after = git_repo / "newfile"
    created_after.write_text("agent created\n")

    checkpoint.revert(str(git_repo), turn_id)

    assert not created_after.exists()


def test_revert_truncates_jsonl_to_offset(
    git_repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transcript = tmp_path / "sess.jsonl"
    transcript.write_text("line1\nline2\n")
    offset = transcript.stat().st_size
    turn_id = new_turn_id()
    monkeypatch.setattr(
        checkpoint,
        "_derive_jsonl_path",
        lambda workdir, session_id: transcript if session_id else None,
    )
    checkpoint.save(
        str(git_repo),
        turn_id,
        cc_session_jsonl_path=str(transcript),
        jsonl_offset=offset,
    )
    with transcript.open("a") as file:
        file.write("line3\nline4\n")

    checkpoint.revert(str(git_repo), turn_id)

    assert transcript.read_text() == "line1\nline2\n"


def test_revert_unknown_turn_raises(git_repo: Path) -> None:
    with pytest.raises(UnknownCheckpointError):
        checkpoint.revert(str(git_repo), "missing")


def test_revert_does_not_touch_other_checkpoints(git_repo: Path) -> None:
    kept_turn = new_turn_id()
    reverted_turn = new_turn_id()
    checkpoint.save(str(git_repo), kept_turn)
    checkpoint.save(str(git_repo), reverted_turn)

    checkpoint.revert(str(git_repo), reverted_turn)

    assert checkpoint_ref_exists(git_repo, kept_turn)


def test_revert_does_not_move_head(git_repo: Path) -> None:
    turn_id = new_turn_id()
    checkpoint.save(str(git_repo), turn_id)
    head_before = git_output(git_repo, "rev-parse", "HEAD")
    (git_repo / "README").write_text("changed\n")

    checkpoint.revert(str(git_repo), turn_id)

    assert git_output(git_repo, "rev-parse", "HEAD") == head_before
