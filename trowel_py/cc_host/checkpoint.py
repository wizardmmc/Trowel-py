"""管理 CC turn 的 Git 快照、恢复与 transcript 截断。"""

from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass
from pathlib import Path

from trowel_py.cc_host import _checkpoint_git as checkpoint_git
from trowel_py.cc_host.session_scan import cc_projects_root, workdir_to_slug

_ENABLE_ENV = "TROWEL_CHECKPOINT_ENABLE"
# 快照可能纳入未忽略的本地文件，必须保持显式 opt-in。


class NotAGitRepoError(RuntimeError):
    """workdir 不在 Git 工作树中。"""


class UnknownCheckpointError(RuntimeError):
    """指定 turn 没有 checkpoint ref。"""


@dataclass(frozen=True)
class CheckpointMeta:
    """持久化在 checkpoint commit message 中的恢复信息。"""

    turn_id: str
    cc_session_id: str | None
    jsonl_offset: int | None
    created_at: str


def is_enabled() -> bool:
    return os.environ.get(_ENABLE_ENV) == "1"


def is_git_repo(workdir: str | os.PathLike) -> bool:
    return checkpoint_git.is_git_repo(workdir)


def save(
    workdir: str | os.PathLike,
    turn_id: str,
    *,
    cc_session_jsonl_path: str | None = None,
    jsonl_offset: int | None = None,
) -> CheckpointMeta:
    """把 turn 前的文件写入私有 ref，不改 HEAD/index/worktree。

    快照包含 tracked 与非忽略 untracked；默认关闭时只返回元数据。
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


def revert(workdir: str | os.PathLike, turn_id: str) -> CheckpointMeta:
    """恢复 index/worktree 并截断 transcript，HEAD 保持不动。"""

    root = _require_repo(workdir)
    commit_oid = checkpoint_git.resolve_checkpoint(root, turn_id)
    if commit_oid is None:
        raise UnknownCheckpointError(turn_id)

    meta = _meta_for_commit(root, turn_id, commit_oid)
    checkpoint_git.restore_checkpoint(root, commit_oid)
    jsonl_path = _derive_jsonl_path(workdir, meta.cc_session_id)
    if jsonl_path is not None and meta.jsonl_offset is not None:
        _truncate_file(str(jsonl_path), meta.jsonl_offset)
    return meta


def list_checkpoints(workdir: str | os.PathLike) -> list[CheckpointMeta]:
    """按创建时间从新到旧读取当前仓库的 checkpoint。"""

    if not is_git_repo(workdir):
        return []
    root = checkpoint_git.top_level(workdir)
    metas = [
        _meta_for_commit(root, refname.rsplit("/", 1)[-1], commit_oid)
        for refname, commit_oid in checkpoint_git.list_checkpoint_refs(root)
    ]
    metas.sort(key=lambda meta: (meta.created_at, meta.turn_id), reverse=True)
    return metas


def gc(workdir: str | os.PathLike, *, keep: int = 50) -> int:
    """删除超过保留数量的旧 checkpoint ref。"""

    if not is_git_repo(workdir):
        return 0
    root = checkpoint_git.top_level(workdir)
    return checkpoint_git.prune_checkpoints(root, keep)


def _require_repo(workdir: str | os.PathLike) -> str:
    if not is_git_repo(workdir):
        raise NotAGitRepoError(f"{workdir} is not a git work tree")
    return checkpoint_git.top_level(workdir)


def _session_id_from_path(jsonl_path: str | None) -> str | None:
    """只提取文件名中的 session id，避免把本机路径写入 Git 历史。"""

    if not jsonl_path:
        return None
    return Path(jsonl_path).stem or None


def _derive_jsonl_path(
    workdir: str | os.PathLike,
    cc_session_id: str | None,
) -> Path | None:
    if not cc_session_id:
        return None
    return (
        cc_projects_root()
        / workdir_to_slug(workdir)
        / f"{cc_session_id}.jsonl"
    )


def _meta_for_commit(
    root: str,
    turn_id: str,
    commit_oid: str,
) -> CheckpointMeta:
    body = checkpoint_git.read_checkpoint_message(root, commit_oid)
    return _decode_message(body, turn_id)


def _truncate_file(path: str, offset: int) -> None:
    """按字节截断；offset 落在半行时回退到上一条完整 JSONL。"""

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
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="microseconds")


def _encode_message(meta: CheckpointMeta) -> str:
    """只序列化 session id，不把绝对 transcript 路径写入 commit。"""

    cc_session_id = meta.cc_session_id or ""
    jsonl_offset = "" if meta.jsonl_offset is None else str(meta.jsonl_offset)
    return (
        f"checkpoint:{meta.turn_id}\n"
        f"cc-session {cc_session_id}\n"
        f"jsonl-offset {jsonl_offset}\n"
        f"created {meta.created_at}\n"
    )


def _decode_message(body: str, turn_id: str) -> CheckpointMeta:
    """容忍缺失和畸形字段；旧版 jsonl-path 不再读取。"""

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
