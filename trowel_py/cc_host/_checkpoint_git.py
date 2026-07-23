"""仅供 checkpoint facade 使用的 Git plumbing 与私有 ref。"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

_REF_NAMESPACE = "refs/trowel-checkpoints"
_IDENTITY_NAME = "Checkpointer"
_IDENTITY_EMAIL = "checkpointer@noreply"


def is_git_repo(workdir: str | os.PathLike) -> bool:
    process = subprocess.run(
        ["git", "-C", str(workdir), "rev-parse", "--is-inside-work-tree"],
        capture_output=True,
    )
    return process.returncode == 0


def top_level(workdir: str | os.PathLike) -> str:
    return _run_git(workdir, "rev-parse", "--show-toplevel").strip()


def create_checkpoint(
    root: str,
    turn_id: str,
    message: str,
    created_at: str,
) -> None:
    index_tree = _run_git(root, "write-tree").strip()
    worktree_tree = _snapshot_worktree_tree(root, index_tree)
    commit_oid = _commit_tree(root, worktree_tree, message, created_at)
    _run_git(root, "update-ref", f"{_REF_NAMESPACE}/{turn_id}", commit_oid)


def resolve_checkpoint(root: str, turn_id: str) -> str | None:
    ref = f"{_REF_NAMESPACE}/{turn_id}"
    process = subprocess.run(
        ["git", "-C", root, "rev-parse", "--verify", "--quiet", ref],
        capture_output=True,
    )
    if process.returncode != 0:
        return None
    return process.stdout.decode().strip()


def restore_checkpoint(root: str, commit_oid: str) -> None:
    tree = _run_git(root, "rev-parse", f"{commit_oid}^{{tree}}").strip()
    # read-tree 同步恢复 index/worktree；clean 只移除非忽略的新增文件。
    _run_git(root, "read-tree", "--reset", "-u", tree)
    _run_git(root, "clean", "-fd")


def list_checkpoint_refs(root: str) -> list[tuple[str, str]]:
    raw = _run_git(
        root,
        "for-each-ref",
        "--format=%(refname) %(objectname)",
        _REF_NAMESPACE,
    )
    refs: list[tuple[str, str]] = []
    for line in raw.splitlines():
        parts = line.strip().split()
        if len(parts) == 2:
            refs.append((parts[0], parts[1]))
    return refs


def prune_checkpoints(root: str, keep: int) -> int:
    raw = _run_git(
        root,
        "for-each-ref",
        "--format=%(refname)",
        "--sort=-committerdate",
        _REF_NAMESPACE,
    )
    refs = [line.strip() for line in raw.splitlines() if line.strip()]
    if len(refs) <= keep:
        return 0
    for ref in refs[keep:]:
        _run_git(root, "update-ref", "-d", ref)
    return len(refs) - keep


def read_checkpoint_message(root: str, commit_oid: str) -> str:
    return _run_git(root, "log", "-1", "--format=%B", commit_oid)


def _run_git(
    cwd: str | os.PathLike,
    *args: str,
    env: dict | None = None,
) -> str:
    process = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        env=env,
    )
    if process.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed "
            f"(rc={process.returncode}): {process.stderr.strip()}"
        )
    return process.stdout


def _snapshot_worktree_tree(root: str, seed_tree: str) -> str:
    temporary_dir = Path(tempfile.mkdtemp(prefix="trowel-cp-"))
    try:
        # GIT_INDEX_FILE 必须指向尚不存在的文件，不能直接用 NamedTemporaryFile。
        temporary_index = temporary_dir / "index"
        env = {**os.environ, "GIT_INDEX_FILE": str(temporary_index)}
        if seed_tree:
            _run_git(root, "read-tree", seed_tree, env=env)
        _run_git(root, "add", "-A", "--", ".", env=env)
        return _run_git(root, "write-tree", env=env).strip()
    finally:
        shutil.rmtree(temporary_dir, ignore_errors=True)


def _commit_tree(root: str, tree: str, message: str, created_at: str) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": _IDENTITY_NAME,
        "GIT_AUTHOR_EMAIL": _IDENTITY_EMAIL,
        "GIT_AUTHOR_DATE": created_at,
        "GIT_COMMITTER_NAME": _IDENTITY_NAME,
        "GIT_COMMITTER_EMAIL": _IDENTITY_EMAIL,
        "GIT_COMMITTER_DATE": created_at,
    }
    process = subprocess.run(
        ["git", "-C", root, "commit-tree", tree],
        input=message,
        capture_output=True,
        text=True,
        env=env,
    )
    if process.returncode != 0:
        raise RuntimeError(f"commit-tree failed: {process.stderr.strip()}")
    return process.stdout.strip()
