"""CC 会话创建、查询与关闭算法。

模块不持有进程级状态；调用方必须显式传入 registry 和多会话索引。
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any, cast

from trowel_py.cc_host.service import CCHost
from trowel_py.cc_host.schemas import CreateSessionRequest


class CcWorkdirNotFoundError(Exception):
    """CC 会话的工作目录不存在。"""


class CcCapacityError(Exception):
    """CC registry 已达到连接上限。"""


def _display_name(workdir: str, workdir_index: dict[str, set[str]]) -> str:
    """按工作目录名和同目录现有会话数生成显示名称。

    Args:
        workdir: 新会话使用的工作目录。
        workdir_index: 各工作目录当前包含的会话 ID。

    Returns:
        首个会话使用目录名，后续会话使用带序号的目录名。
    """

    basename = Path(workdir).name or workdir
    existing = len(workdir_index.get(workdir, ()))
    return basename if existing == 0 else f"{basename} #{existing + 1}"


def open_session(
    req: CreateSessionRequest,
    registry: dict[str, CCHost],
    *,
    proxy_base_url: str | None,
    settings_path: str | Path | None,
    workdir_index: dict[str, set[str]],
    session_names: dict[str, str],
    max_connections: int,
    host_factory: Any,
) -> tuple[str, CCHost, str]:
    """创建主机并写入调用方持有的会话状态。"""

    if not Path(req.workdir).is_dir():
        raise CcWorkdirNotFoundError("workdir does not exist")
    if len(registry) >= max_connections:
        raise CcCapacityError(
            f"连接数已达上限（{max_connections}），请先关闭一些 session"
        )
    sid = uuid.uuid4().hex

    from trowel_py.memory.mcp_config import write_mcp_config

    from trowel_py.memory.paths import resolve_memory_root

    port = os.environ.get("TROWEL_SERVER_PORT", "8000")
    mcp_config = str(
        write_mcp_config(
            trowel_session_id=sid,
            runtime="claude_code",
            workdir=req.workdir,
            permission=req.permission_mode,
            memory_enabled=req.memory_enabled,
            agent_mcp_enabled=req.agent_mcp_enabled,
            memory_root=str(resolve_memory_root()),
            base_url=f"http://127.0.0.1:{port}",
            profile_enabled=req.profile_enabled,
            self_enabled=req.self_enabled,
            delegation_depth=req.delegation_depth,
        )
    )
    try:
        host = host_factory(
            sid,
            req.workdir,
            model=req.model,
            effort=req.effort,
            permission_mode=req.permission_mode,
            resume_from=req.resume_from,
            proxy_base_url=proxy_base_url,
            settings_path=settings_path,
            mcp_config=mcp_config,
            owned_mcp_config=True,
            session_kind=req.session_kind,
            agent_mcp_enabled=req.agent_mcp_enabled,
            memory_enabled=req.memory_enabled,
            profile_enabled=req.profile_enabled,
            self_enabled=req.self_enabled,
        )
    except BaseException:
        Path(mcp_config).unlink(missing_ok=True)
        raise
    registry[sid] = host
    name = _display_name(req.workdir, workdir_index)
    workdir_index.setdefault(req.workdir, set()).add(sid)
    session_names[sid] = name
    return sid, host, name


def list_live_sessions(
    registry: dict[str, CCHost],
    session_names: dict[str, str],
) -> list[dict[str, object]]:
    """把当前 registry 转为路由响应使用的会话摘要。

    Args:
        registry: 当前会话 ID 与 CC host 的对应表。
        session_names: 各会话 ID 对应的显示名称；缺失时使用工作目录名。

    Returns:
        保持 registry 迭代顺序的会话摘要列表。
    """

    return [
        {
            "id": sid,
            "workdir": host.workdir,
            "model": host.model,
            "name": session_names.get(sid, Path(host.workdir).name),
            "running": getattr(host, "running", False),
            "connected": not getattr(host, "is_dead", True),
            "memory_enabled": getattr(host, "memory_enabled", True),
            "profile_enabled": getattr(host, "profile_enabled", True),
        }
        for sid, host in registry.items()
    ]


def init_roster_for_workdir(
    workdir: str,
    registry: dict[str, CCHost],
    workdir_index: dict[str, set[str]],
    active_session_id: str | None,
) -> list[str]:
    """返回指定工作目录中第一个非空的 CC 初始化命令表。

    当前活跃会话属于该目录时优先读取它，否则检查同目录的其他会话。

    Args:
        workdir: 要查询的工作目录。
        registry: 当前会话 ID 与 CC host 的对应表。
        workdir_index: 各工作目录当前包含的会话 ID。
        active_session_id: 当前活跃的会话 ID；`None` 表示没有活跃会话。

    Returns:
        找到的首个非空命令列表；没有可用命令时返回空列表。
    """

    sids = workdir_index.get(workdir, set())
    ordered = ([active_session_id] if active_session_id in sids else []) + [
        sid for sid in sids if sid != active_session_id
    ]
    for sid in ordered:
        roster = getattr(registry.get(sid), "_init_roster", None)
        if roster:
            return roster
    return []


async def close_session(
    session_id: str,
    registry: dict[str, CCHost],
    *,
    workdir_index: dict[str, set[str]],
    session_names: dict[str, str],
) -> bool:
    """关闭 CC host，并从 registry、工作目录索引和名称表中移除会话。

    未知会话不修改任何状态。host 关闭失败时异常向上传递，三张表保持不变。

    Args:
        session_id: 要关闭的 Trowel 会话 ID。
        registry: 当前会话 ID 与 CC host 的对应表。
        workdir_index: 各工作目录当前包含的会话 ID。
        session_names: 各会话 ID 对应的显示名称。

    Returns:
        找到并成功关闭会话时返回 `True`；会话不存在时返回 `False`。

    Raises:
        BaseException: host 关闭失败时原样向上传递。
    """

    host = registry.get(session_id)
    if host is None:
        return False
    await host.close()
    registry.pop(session_id, None)
    workdir = cast(str, host.workdir)
    if session_id in workdir_index.get(workdir, set()):
        workdir_index[workdir].discard(session_id)
        if not workdir_index[workdir]:
            workdir_index.pop(workdir, None)
    session_names.pop(session_id, None)
    return True
