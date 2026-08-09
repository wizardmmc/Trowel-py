"""保存和恢复 Claude Code 轮次开始前的 Git 文件快照，并同步回退会话日志。"""

from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass
from pathlib import Path

from trowel_py.cc_host.checkpoint import git as checkpoint_git
from trowel_py.cc_host.session_scan import cc_projects_root, workdir_to_slug

_ENABLE_ENV = "TROWEL_CHECKPOINT_ENABLE"
# 快照会包含未被 Git 忽略的本地文件，因此必须由环境变量显式开启。


class NotAGitRepoError(RuntimeError):
    """表示工作目录不属于任何 Git 工作树。"""


class UnknownCheckpointError(RuntimeError):
    """表示指定轮次没有可恢复的 checkpoint ref。"""


@dataclass(frozen=True)
class CheckpointMeta:
    """记录 checkpoint commit message 中的恢复信息。

    Attributes:
        turn_id: Trowel 分配的逻辑轮次 ID，同时用作私有 ref 的末段。
        cc_session_id: 用于重新定位会话日志的 Claude Code 会话 ID；未记录时为
            None。
        jsonl_offset: 轮次开始前的非负日志字节位置；未记录或无法解析时为 None。
            只有它与 ``cc_session_id`` 都非 None 时，恢复操作才会尝试截断日志。
        created_at: checkpoint commit 的创建时间。新快照使用保留微秒的 ISO 8601
            UTC 时间；旧元数据缺失该字段时为空字符串。``list_checkpoints`` 用它
            排序。
    """

    turn_id: str
    cc_session_id: str | None
    jsonl_offset: int | None
    created_at: str


def is_enabled() -> bool:
    """判断 ``TROWEL_CHECKPOINT_ENABLE`` 是否精确设为 ``1``。"""

    return os.environ.get(_ENABLE_ENV) == "1"


def is_git_repo(workdir: str | os.PathLike[str]) -> bool:
    """判断工作目录是否位于 Git 工作树中。"""

    return checkpoint_git.is_git_repo(workdir)


def save(
    workdir: str | os.PathLike[str],
    turn_id: str,
    *,
    cc_session_jsonl_path: str | None = None,
    jsonl_offset: int | None = None,
) -> CheckpointMeta:
    """为指定轮次记录恢复信息，并在功能开启时保存当前文件快照。

    快照包含已跟踪文件和未被 Git 忽略的未跟踪文件，写入私有 ref，不改变
    HEAD、index 或工作区。功能关闭时仍校验仓库并返回元数据，但不创建 ref。

    Args:
        workdir: 该轮次运行所在的 Git 工作目录。
        turn_id: Trowel 分配的逻辑轮次 ID，同时用作私有 ref 的末段。
        cc_session_jsonl_path: Claude Code 会话日志路径；只提取文件名中的会话 ID，
            不把本机路径写入 commit message。没有关联日志时为 None。
        jsonl_offset: 轮次开始前会话日志的字节长度，恢复时截断到此位置；不需要
            回退日志时为 None。

    Returns:
        本次快照的恢复信息；功能关闭时也会返回。

    Raises:
        NotAGitRepoError: ``workdir`` 不属于 Git 工作树。
        RuntimeError: 创建或更新 checkpoint 所需的 Git 命令失败。
        OSError: 无法启动 Git 或创建临时 index。
    """

    root = _require_repo(workdir)
    created_at = _now_iso()
    meta = CheckpointMeta(
        turn_id=turn_id,
        cc_session_id=_session_id_from_path(cc_session_jsonl_path),
        jsonl_offset=jsonl_offset,
        created_at=created_at,
    )
    if not is_enabled():
        return meta

    checkpoint_git.create_checkpoint(
        root,
        turn_id,
        _encode_message(meta),
        created_at,
    )
    return meta


def revert(
    workdir: str | os.PathLike[str],
    turn_id: str,
    *,
    projects_root: Path | None = None,
) -> CheckpointMeta:
    """把 index、工作区和会话日志恢复到指定轮次开始前。

    恢复会用 checkpoint 的单一文件树重写 index 和工作区。checkpoint 之后对
    快照内文件的修改和删除会丢失，之后新增且未被 Git 忽略的文件和目录会被
    删除；HEAD 和用户分支的提交历史不变。该操作恢复文件内容，但不恢复保存时
    的暂存边界。

    关联日志存在且长于记录位置时，会删除该位置之后的内容。Git 恢复先于日志
    截断，两步不是原子操作。

    Args:
        workdir: 保存 checkpoint 时使用的 Claude Code 工作目录；用于确定 Git
            仓库并重新定位会话日志。
        turn_id: 要恢复到的 Trowel 逻辑轮次 ID。
        projects_root: 会话所属 Claude 家的 projects 根；None 使用真实全局根。

    Returns:
        checkpoint 中读取到的恢复信息。

    Raises:
        NotAGitRepoError: ``workdir`` 不属于 Git 工作树。
        UnknownCheckpointError: 指定轮次没有 checkpoint ref。
        RuntimeError: 读取或恢复 checkpoint 所需的 Git 命令失败。
        OSError: 无法启动 Git 或读写会话日志。
    """

    root = _require_repo(workdir)
    commit_oid = checkpoint_git.resolve_checkpoint(root, turn_id)
    if commit_oid is None:
        raise UnknownCheckpointError(turn_id)

    meta = _meta_for_commit(root, turn_id, commit_oid)
    checkpoint_git.restore_checkpoint(root, commit_oid)
    jsonl_path = (
        _derive_jsonl_path(workdir, meta.cc_session_id)
        if projects_root is None
        else _derive_jsonl_path(
            workdir,
            meta.cc_session_id,
            projects_root=projects_root,
        )
    )
    if jsonl_path is not None and meta.jsonl_offset is not None:
        _truncate_file(str(jsonl_path), meta.jsonl_offset)
    return meta


def list_checkpoints(workdir: str | os.PathLike[str]) -> list[CheckpointMeta]:
    """按创建时间从新到旧读取工作目录所属仓库的 checkpoint。

    路径不属于 Git 工作树时返回空列表。

    Args:
        workdir: 用于确定目标 Git 仓库的工作目录。

    Returns:
        仓库中可读取的 checkpoint 恢复信息。

    Raises:
        RuntimeError: 枚举或读取 checkpoint ref 所需的 Git 命令失败。
        OSError: 无法启动 Git。
    """

    if not is_git_repo(workdir):
        return []
    root = checkpoint_git.top_level(workdir)
    metas = [
        _meta_for_commit(root, refname.rsplit("/", 1)[-1], commit_oid)
        for refname, commit_oid in checkpoint_git.list_checkpoint_refs(root)
    ]
    metas.sort(key=lambda meta: (meta.created_at, meta.turn_id), reverse=True)
    return metas


def gc(workdir: str | os.PathLike[str], *, keep: int = 50) -> int:
    """删除超过保留数量的旧 checkpoint ref。

    Args:
        workdir: 用于确定目标 Git 仓库的工作目录。
        keep: 从最新开始保留的 checkpoint ref 数量；必须为非负数，0 表示全部
            删除。

    Returns:
        实际删除的 ref 数量；路径不属于 Git 工作树时为 0。

    Raises:
        RuntimeError: 枚举或删除 checkpoint ref 所需的 Git 命令失败。
        OSError: 无法启动 Git。
    """

    if not is_git_repo(workdir):
        return 0
    root = checkpoint_git.top_level(workdir)
    return checkpoint_git.prune_checkpoints(root, keep)


def _require_repo(workdir: str | os.PathLike[str]) -> str:
    """取得工作目录所属 Git 工作树的根目录。

    Args:
        workdir: 要解析的工作目录。

    Returns:
        Git 工作树根目录。

    Raises:
        NotAGitRepoError: ``workdir`` 不属于 Git 工作树。
    """

    if not is_git_repo(workdir):
        raise NotAGitRepoError(f"{workdir} is not a git work tree")
    return checkpoint_git.top_level(workdir)


def _session_id_from_path(jsonl_path: str | None) -> str | None:
    """从会话日志文件名提取 Claude Code 会话 ID。

    只保留文件名 stem，避免把本机路径写入私有 checkpoint commit 的消息正文。

    Args:
        jsonl_path: Claude Code 会话日志路径；没有日志时为 None。

    Returns:
        不含扩展名的文件名；输入为空或文件名为空时为 None。
    """

    if not jsonl_path:
        return None
    return Path(jsonl_path).stem or None


def _derive_jsonl_path(
    workdir: str | os.PathLike[str],
    cc_session_id: str | None,
    *,
    projects_root: Path | None = None,
) -> Path | None:
    """根据工作目录和 Claude Code 会话 ID 得到会话日志路径。

    Args:
        workdir: 会话运行所在的工作目录。
        cc_session_id: Claude Code 会话 ID；尚未取得时为 None。
        projects_root: 会话所属 Claude 家的 projects 根；None 使用真实全局根。

    Returns:
        预计的 JSONL 日志路径；会话 ID 为空时为 None。
    """

    if not cc_session_id:
        return None
    return (
        (projects_root or cc_projects_root())
        / workdir_to_slug(workdir)
        / f"{cc_session_id}.jsonl"
    )


def _meta_for_commit(
    root: str,
    turn_id: str,
    commit_oid: str,
) -> CheckpointMeta:
    """从 checkpoint commit message 中读取指定轮次的恢复信息。

    Args:
        root: Git 工作树根目录。
        turn_id: checkpoint 所属的 Trowel 逻辑轮次 ID。
        commit_oid: 保存 checkpoint 的 commit 对象 ID。

    Returns:
        从 commit message 解码出的恢复信息。
    """

    body = checkpoint_git.read_checkpoint_message(root, commit_oid)
    return _decode_message(body, turn_id)


def _truncate_file(path: str, offset: int) -> None:
    """把会话日志截断到指定字节位置之前的最后一个完整行边界。

    文件不存在或指定位置不小于文件长度时不做处理。

    Args:
        path: 要截断的 JSONL 会话日志路径。
        offset: 轮次开始前记录的字节长度；落在行中间时会回退到上一行末尾。
    """

    transcript = Path(path)
    if not transcript.is_file():
        return
    with transcript.open("r+b") as file:
        file.seek(0, os.SEEK_END)
        size = file.tell()
        if offset >= size:
            return

        cut = offset
        if offset > 0:
            file.seek(offset - 1)
            if file.read(1) != b"\n":
                file.seek(0)
                previous_bytes = file.read(offset)
                last_newline = previous_bytes.rfind(b"\n")
                cut = last_newline + 1 if last_newline >= 0 else 0
        file.truncate(cut)


def _now_iso() -> str:
    """返回带微秒的当前 UTC 时间。"""

    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="microseconds")


def _encode_message(meta: CheckpointMeta) -> str:
    """把恢复信息编码为不含本机会话日志路径的 commit message。

    Args:
        meta: 要持久化的 checkpoint 恢复信息。

    Returns:
        可写入 checkpoint commit 的消息正文。
    """

    cc_session_id = meta.cc_session_id or ""
    jsonl_offset = "" if meta.jsonl_offset is None else str(meta.jsonl_offset)
    return (
        f"checkpoint:{meta.turn_id}\n"
        f"cc-session {cc_session_id}\n"
        f"jsonl-offset {jsonl_offset}\n"
        f"created {meta.created_at}\n"
    )


def _decode_message(body: str, turn_id: str) -> CheckpointMeta:
    """从 commit message 解码 checkpoint 恢复信息。

    ``turn_id`` 直接使用参数，不读取正文中的 ``checkpoint:`` 行。缺少
    ``cc-session``、``jsonl-offset`` 或 ``created`` 时分别得到 None、None 或
    空字符串；无法解析的 ``jsonl-offset`` 也得到 None。``created`` 不校验格式，
    旧版 ``jsonl-path`` 始终忽略。

    Args:
        body: checkpoint commit 的完整消息正文。
        turn_id: 当前 checkpoint ref 对应的 Trowel 逻辑轮次 ID。

    Returns:
        解码后的恢复信息。
    """

    cc_session_id: str | None = None
    jsonl_offset: int | None = None
    created_at = ""
    for line in body.splitlines():
        if line.startswith("cc-session "):
            value = line[len("cc-session ") :].strip()
            cc_session_id = value or None
        elif line.startswith("jsonl-offset "):
            value = line[len("jsonl-offset ") :].strip()
            if value:
                try:
                    jsonl_offset = int(value)
                except ValueError:
                    jsonl_offset = None
        elif line.startswith("created "):
            created_at = line[len("created ") :].strip()
    return CheckpointMeta(
        turn_id=turn_id,
        cc_session_id=cc_session_id,
        jsonl_offset=jsonl_offset,
        created_at=created_at,
    )
