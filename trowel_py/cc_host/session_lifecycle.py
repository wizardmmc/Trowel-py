"""CC 会话创建、查询与关闭算法。

模块不持有进程级状态；调用方必须显式传入 registry 和多会话索引。
"""

from __future__ import annotations

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

    mcp_config = str(write_mcp_config()) if req.memory_enabled else None
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
        memory_enabled=req.memory_enabled,
        profile_enabled=req.profile_enabled,
        self_enabled=req.self_enabled,
    )
    registry[sid] = host
    name = _display_name(req.workdir, workdir_index)
    workdir_index.setdefault(req.workdir, set()).add(sid)
    session_names[sid] = name
    return sid, host, name


def list_live_sessions(
    registry: dict[str, CCHost],
    session_names: dict[str, str],
) -> list[dict[str, object]]:
    """把当前 registry 转为路由响应所需的会话行。"""

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
    """优先读取指定工作目录当前活跃会话的初始化命令表。"""

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
    """关闭主机，并从调用方持有的多会话状态中移除。"""

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
