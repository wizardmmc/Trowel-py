"""为 CC 会话写入不依赖 MCP SDK 的 composite stdio MCP 配置。"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

from trowel_py.agent_mcp.launch import build_agent_mcp_launch_spec
from trowel_py.application_paths import resolve_application_data_root


def _config_path(trowel_session_id: str) -> Path:
    """按环境变量优先级确定当前会话的 MCP 配置路径。

    ``TROWEL_MCP_CONFIG`` 非空时优先使用：会话 ID 为空则原样返回该路径；
    否则在同目录的文件 stem 后追加 ``-<session_id>``，保留原后缀，无后缀时
    补 ``.json``。未设置该变量时，文件写入 ``TROWEL_MCP_CONFIG_DIR``；目录
    变量也未设置时使用 ``~/.trowel/mcp-configs``。文件名是
    ``<session_id>.json``，空会话 ID 则为 ``memory.json``。目录变量被显式
    设为空字符串时，``Path("")`` 指向当前目录，不会使用默认目录。

    会话 ID 不做文件名清洗；调用方必须提供不含路径分隔符或 ``..`` 的安全文件
    名片段，并自行避免不同会话写入同一路径。

    Args:
        trowel_session_id: 用于区分配置文件的 Trowel 会话 ID；空字符串表示使用
            共享的无会话路径。

    Returns:
        根据环境和会话 ID 计算出的目标配置路径。
    """
    legacy = os.environ.get("TROWEL_MCP_CONFIG")
    if legacy:
        legacy_path = Path(legacy)
        if not trowel_session_id:
            return legacy_path
        return legacy_path.with_name(
            f"{legacy_path.stem}-{trowel_session_id}{legacy_path.suffix or '.json'}"
        )
    directory = Path(
        os.environ.get(
            "TROWEL_MCP_CONFIG_DIR",
            str(resolve_application_data_root() / "mcp-configs"),
        )
    )
    name = f"{trowel_session_id}.json" if trowel_session_id else "memory.json"
    return directory / name


def write_mcp_config(
    *,
    trowel_session_id: str = "",
    runtime: str = "claude_code",
    workdir: str = "",
    permission: str = "",
    memory_enabled: bool = True,
    agent_mcp_enabled: bool = False,
    memory_root: str = "",
    base_url: str = "",
    profile_enabled: bool = True,
    self_enabled: bool = True,
    delegation_depth: int = 0,
    agent_api_credential: str = "",
) -> Path:
    """按会话写入 memory/agent MCP roster，并返回隔离配置路径。

    Args:
        trowel_session_id: 当前 Trowel 会话 ID；空字符串使用共享配置名。
        runtime: 当前会话的运行时。
        workdir: Agent MCP 委派子会话时继承的工作目录。
        permission: Agent MCP 委派子会话时继承的权限模式。
        memory_enabled: 是否挂载 Memory MCP 并允许子会话继承 Memory。
        agent_mcp_enabled: 是否挂载 Agent MCP。
        memory_root: Memory MCP 和子会话使用的记忆根目录。
        base_url: Agent MCP 回调 Agent Host 的地址。
        profile_enabled: 子会话是否继承用户画像。
        self_enabled: 子会话是否继承持续身份。
        delegation_depth: 当前会话的委派深度。
        agent_api_credential: 桌面模式下 Agent MCP 访问 Agent Host 的实例凭据。

    Returns:
        已写入的隔离 MCP 配置文件路径。
    """

    path = _config_path(trowel_session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    servers: dict[str, object] = {}
    if memory_enabled:
        servers["memory"] = {
            "type": "stdio",
            "command": sys.executable,
            "args": ["-m", "trowel_py.memory.mcp_server"],
        }
    if agent_mcp_enabled:
        agent_env = {"MEMORY_ROOT": memory_root}
        if agent_api_credential:
            agent_env["TROWEL_RESOURCE_REGISTRATION_CREDENTIAL"] = agent_api_credential
        launch = build_agent_mcp_launch_spec(
            trowel_session_id=trowel_session_id,
            runtime=runtime,
            workdir=workdir,
            permission=permission,
            base_url=base_url,
            memory_enabled=memory_enabled,
            profile_enabled=profile_enabled,
            self_enabled=self_enabled,
            delegation_depth=delegation_depth,
            extra_env=agent_env,
        )
        servers[launch.server_name] = {
            "type": "stdio",
            "command": launch.command,
            "args": list(launch.module_args),
            "alwaysLoad": True,
            "env": dict(launch.env),
        }
    config = {"mcpServers": servers}
    _write_private_config(path, config)
    return path


def _write_private_config(path: Path, config: dict[str, object]) -> None:
    """原子写入只允许当前账号读取的 MCP 配置。

    Agent MCP 配置可能包含桌面实例凭据。临时文件从创建起即使用 ``0600``，再原子
    替换目标，避免覆盖旧配置时沿用较宽权限，也避免 Claude Code 读取到半份 JSON。

    Args:
        path: 最终 MCP 配置路径。
        config: 已组装完成、可序列化为 JSON 的配置对象。
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        os.chmod(temporary, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = -1
            json.dump(config, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)
        raise
