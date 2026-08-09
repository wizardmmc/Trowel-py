"""通过 Git plumbing 把轮次文件快照写入私有 ref，并恢复 index 和工作区。"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

_REF_NAMESPACE = "refs/trowel-checkpoints"
_IDENTITY_NAME = "Checkpointer"
_IDENTITY_EMAIL = "checkpointer@noreply"


def is_git_repo(workdir: str | os.PathLike[str]) -> bool:
    """通过 Git 判断路径是否位于工作树中。"""

    process = subprocess.run(
        ["git", "-C", str(workdir), "rev-parse", "--is-inside-work-tree"],
        capture_output=True,
    )
    return process.returncode == 0


def top_level(workdir: str | os.PathLike[str]) -> str:
    """返回路径所属 Git 工作树的根目录。"""

    return _run_git(workdir, "rev-parse", "--show-toplevel").strip()


def create_checkpoint(
    root: str,
    turn_id: str,
    message: str,
    created_at: str,
) -> None:
    """把当前文件状态保存为指定轮次的私有 checkpoint ref。

    临时 index 会把当前 index、工作区和未被忽略的未跟踪文件合成一棵 Git tree，
    再写入没有 parent 的 commit。此过程不改变用户的 HEAD、index 或工作区。

    Args:
        root: 轮次所在 Git 工作树的根目录。
        turn_id: Trowel 分配的逻辑轮次 ID，同时用作私有 ref 的末段。
        message: 要写入 checkpoint commit 的恢复信息。
        created_at: checkpoint commit 的 author 和 committer 时间。
    """

    index_tree = _run_git(root, "write-tree").strip()
    worktree_tree = _snapshot_worktree_tree(root, index_tree)
    commit_oid = _commit_tree(root, worktree_tree, message, created_at)
    _run_git(root, "update-ref", f"{_REF_NAMESPACE}/{turn_id}", commit_oid)


def resolve_checkpoint(root: str, turn_id: str) -> str | None:
    """查找指定轮次的 checkpoint commit 对象 ID。

    Args:
        root: Git 工作树根目录。
        turn_id: Trowel 分配的逻辑轮次 ID。

    Returns:
        私有 ref 指向的 commit 对象 ID；ref 不存在或无法解析时为 None。
    """

    ref = f"{_REF_NAMESPACE}/{turn_id}"
    process = subprocess.run(
        ["git", "-C", root, "rev-parse", "--verify", "--quiet", ref],
        capture_output=True,
    )
    if process.returncode != 0:
        return None
    return process.stdout.decode().strip()


def restore_checkpoint(root: str, commit_oid: str) -> None:
    """用 checkpoint 的文件树重写 index 和工作区，但不移动 HEAD。

    恢复会覆盖 checkpoint 后的文件修改，并删除之后新增且未被 Git 忽略的文件和
    普通目录；嵌套 Git 仓库目录不会被 ``git clean -fd`` 删除。checkpoint 只保存
    一棵文件树，因此无法恢复原来的暂存边界。

    Args:
        root: 要恢复的 Git 工作树根目录。
        commit_oid: 保存目标文件树的 checkpoint commit 对象 ID。
    """

    tree = _run_git(root, "rev-parse", f"{commit_oid}^{{tree}}").strip()
    # read-tree 重写 index 并更新工作区；clean 删除未忽略的未跟踪文件和普通目录。
    _run_git(root, "read-tree", "--reset", "-u", tree)
    _run_git(root, "clean", "-fd")


def list_checkpoint_refs(root: str) -> list[tuple[str, str]]:
    """列出仓库中的 checkpoint ref 及其 commit 对象 ID。

    Args:
        root: Git 工作树根目录。

    Returns:
        由完整 ref 名和 commit 对象 ID 组成的列表。
    """

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
    """按 committer 时间倒序保留指定数量的 checkpoint ref。

    Args:
        root: Git 工作树根目录。
        keep: 按 committer 时间排序后要保留的 ref 数量；必须为非负数，0 表示
            全部删除。时间相同的 ref 之间不保证顺序。

    Returns:
        实际删除的 ref 数量。
    """

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
    """读取 checkpoint commit 的完整消息。

    Args:
        root: Git 工作树根目录。
        commit_oid: checkpoint commit 对象 ID。

    Returns:
        commit message 正文。
    """

    return _run_git(root, "log", "-1", "--format=%B", commit_oid)


def _run_git(
    cwd: str | os.PathLike[str],
    *args: str,
    env: dict[str, str] | None = None,
) -> str:
    """在指定目录运行 Git 命令并返回标准输出。

    ``env`` 非 None 时会替换子进程环境，调用方需要自行保留仍然需要的环境变量。

    Args:
        cwd: 传给 ``git -C`` 的工作目录。
        *args: ``git -C <cwd>`` 之后的命令参数。
        env: Git 子进程使用的完整环境；None 表示继承当前进程环境。

    Returns:
        Git 命令未去除空白的标准输出。

    Raises:
        RuntimeError: Git 命令以非零状态退出。
        OSError: 无法启动 Git 进程。
    """

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
    """通过临时 index 生成包含当前文件状态的 Git tree。

    临时 index 先载入 ``seed_tree``，再用工作区和未被忽略的未跟踪文件覆盖；因此
    返回的 tree 不保留原 index 与工作区之间的暂存边界。

    Args:
        root: Git 工作树根目录。
        seed_tree: 当前 index 对应的 Git tree 对象 ID；空值表示不预载文件树。

    Returns:
        合并当前文件状态后生成的 Git tree 对象 ID。
    """

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
    """用固定身份把 Git tree 和恢复信息写成无 parent 的 checkpoint commit。

    Args:
        root: Git 工作树根目录。
        tree: 要保存的 Git tree 对象 ID。
        message: checkpoint commit 的消息正文。
        created_at: author 和 committer 共用的时间。

    Returns:
        新建 checkpoint commit 的对象 ID。

    Raises:
        RuntimeError: ``git commit-tree`` 以非零状态退出。
        OSError: 无法启动 Git 进程。
    """

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
