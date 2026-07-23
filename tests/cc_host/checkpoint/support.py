import subprocess
import uuid
from pathlib import Path


def checkpoint_ref_exists(repo: Path, turn_id: str) -> bool:
    process = subprocess.run(
        [
            "git",
            "rev-parse",
            "--verify",
            "--quiet",
            f"refs/trowel-checkpoints/{turn_id}",
        ],
        cwd=repo,
        capture_output=True,
    )
    return process.returncode == 0


def git_output(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def new_turn_id() -> str:
    return uuid.uuid4().hex
