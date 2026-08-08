"""CC 会话创建、查询与关闭算法。

模块不持有进程级状态；调用方必须显式传入 registry 和多会话索引。
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any, cast

from trowel_py.agent_capacity import DISCUSSION_CONNECTION_LIMIT
from trowel_py.agent_host.store import next_session_display_name
from trowel_py.cc_host.service import CCHost
from trowel_py.cc_host.schemas import CreateSessionRequest
from trowel_py.resource_lifecycle.processes import ProcessController
from trowel_py.resource_lifecycle.registry import ResourceRegistry


class CcWorkdirNotFoundError(Exception):
    """CC 会话的工作目录不存在。"""


class CcCapacityError(Exception):
    """CC registry 已达到连接上限。"""


def _display_name(
    workdir: str,
    registry: dict[str, CCHost],
    workdir_index: dict[str, set[str]],
    session_names: dict[str, str],
) -> str:
    """按同目录已登记的用户会话生成显示名称。

    Args:
        workdir: 新会话使用的工作目录。
        registry: 当前会话 ID 与 CC host 的对应表。
        workdir_index: 各工作目录当前包含的会话 ID。
        session_names: 各会话 ID 已经使用的显示名称。

    Returns:
        当前未使用的最小临时会话编号。
    """

    occupied_names = (
        session_names[sid]
        for sid in workdir_index.get(workdir, ())
        if sid in session_names
        and (host := registry.get(sid)) is not None
        and host.session_kind == "user"
    )
    return next_session_display_name(workdir, occupied_names)


def open_session(
    req: CreateSessionRequest,
    registry: dict[str, CCHost],
    *,
    proxy_base_url: str | None,
    settings_path: str | Path | None,
    claude_config_dir: str | Path | None = None,
    claude_plugin_dir: str | Path | None = None,
    workdir_index: dict[str, set[str]],
    session_names: dict[str, str],
    max_connections: int,
    max_delegate_connections: int,
    host_factory: Any,
    display_name: str | None = None,
    process_controller: ProcessController | None = None,
    resource_registry: ResourceRegistry | None = None,
    owned_settings_path: bool = False,
    close_callback: Any | None = None,
    memory_mcp_enabled: bool | None = None,
    bootstrap_context: str | None = None,
    memory_eligibility: bool = True,
    max_discussion_connections: int = DISCUSSION_CONNECTION_LIMIT,
) -> tuple[str, CCHost, str]:
    """按会话类别检查连接池后，创建主机并写入调用方状态。

    Args:
        req: 会话启动配置。
        registry: 接收新 host 的实时会话表。
        proxy_base_url: Claude Code 使用的本地代理地址。
        settings_path: 读取模型服务商环境变量的配置路径。
        claude_config_dir: 该会话冻结使用的 Claude 用户配置目录；
            None 表示兼容旧会话，继续使用 ``~/.claude``。
        claude_plugin_dir: 连接共享的 Claude 插件缓存目录；None 表示
            沿用 Claude Code 默认行为。
        workdir_index: 工作目录到会话 ID 的索引。
        session_names: 会话 ID 到临时显示名称的索引。
        max_connections: 用户会话连接上限。
        max_delegate_connections: 委派会话连接上限。
        host_factory: 构造单会话 host 的工厂。
        display_name: 上层已经分配的显示名称。
        process_controller: 核验并终止独立进程组的实现。
        resource_registry: 登记会话临时资源的应用账本。
        owned_settings_path: 是否由新 host 清理传入的私有 settings。
        close_callback: host 关闭或创建回滚后执行的一次性清理函数。
        memory_mcp_enabled: 是否挂载 Memory MCP；None 时沿用正文注入开关。
        bootstrap_context: 应用内部提供的系统级首轮背景。
        memory_eligibility: 是否允许整个原生会话进入 Memory/Profile 来源。
        max_discussion_connections: 研讨 participant 的独立连接上限。
    """

    if not Path(req.workdir).is_dir():
        raise CcWorkdirNotFoundError("workdir does not exist")
    if req.session_kind == "user":
        pool = "user"
        limit = max_connections
    elif req.session_kind == "discussion":
        pool = "discussion"
        limit = max_discussion_connections
    else:
        pool = "internal"
        limit = max_delegate_connections
    same_kind_connections = sum(
        1
        for host in registry.values()
        if (
            "user"
            if host.session_kind == "user"
            else "discussion"
            if host.session_kind == "discussion"
            else "internal"
        )
        == pool
    )
    if same_kind_connections >= limit:
        if pool == "discussion":
            raise CcCapacityError(f"当前研讨参与者数量已满：连接上限为 {limit}")
        if pool == "internal":
            raise CcCapacityError(f"当前委派数量已满：连接上限为 {limit}")
        raise CcCapacityError(f"连接数已达上限（{limit}），请先关闭一些 session")
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
            memory_enabled=(
                req.memory_enabled if memory_mcp_enabled is None else memory_mcp_enabled
            ),
            agent_mcp_enabled=req.agent_mcp_enabled,
            memory_root=str(resolve_memory_root()),
            base_url=f"http://127.0.0.1:{port}",
            profile_enabled=req.profile_enabled,
            self_enabled=req.self_enabled,
            delegation_depth=req.delegation_depth,
            agent_api_credential=(
                resource_registry.registration_credential
                if resource_registry is not None
                else ""
            ),
        )
    )
    claude_home_config: dict[str, str | Path | None] = {}
    if claude_config_dir is not None:
        claude_home_config["claude_config_dir"] = claude_config_dir
    if claude_plugin_dir is not None:
        claude_home_config["claude_plugin_dir"] = claude_plugin_dir
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
            **claude_home_config,
            owned_settings_path=owned_settings_path,
            close_callback=close_callback,
            mcp_config=mcp_config,
            owned_mcp_config=True,
            session_kind=req.session_kind,
            memory_eligibility=memory_eligibility,
            agent_mcp_enabled=req.agent_mcp_enabled,
            memory_enabled=req.memory_enabled,
            profile_enabled=req.profile_enabled,
            self_enabled=req.self_enabled,
            bootstrap_context=bootstrap_context,
            process_controller=process_controller,
            resource_registry=resource_registry,
        )
    except BaseException:
        Path(mcp_config).unlink(missing_ok=True)
        raise
    registry[sid] = host
    name = display_name or _display_name(
        req.workdir,
        registry,
        workdir_index,
        session_names,
    )
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
        if getattr(host, "session_kind", "user") == "user"
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
        host = registry.get(sid)
        if host is None or getattr(host, "session_kind", "user") != "user":
            continue
        roster = getattr(host, "_init_roster", None)
        if roster:
            return roster
    return []


def discard_unstarted_session(
    session_id: str,
    registry: dict[str, CCHost],
    *,
    workdir_index: dict[str, set[str]],
    session_names: dict[str, str],
) -> bool:
    """撤销尚未启动的会话创建，并同步移除三张注册表。

    该同步入口只用于创建后的持久化失败；已经启动的会话必须走异步
    ``close_session``，避免跳过进程和后台任务清理。
    """

    host = registry.get(session_id)
    if host is None:
        return False
    host.discard_unstarted()
    registry.pop(session_id, None)
    workdir = cast(str, host.workdir)
    if session_id in workdir_index.get(workdir, set()):
        workdir_index[workdir].discard(session_id)
        if not workdir_index[workdir]:
            workdir_index.pop(workdir, None)
    session_names.pop(session_id, None)
    return True


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
