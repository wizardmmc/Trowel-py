"""检测会破坏 memory-off 隔离的同名 MCP server。

``--disable memories`` 不会注销用户配置的 MCP server。同名配置仍可能在
memory-off thread 中启动，因此 hub 必须在创建 session 前检查 global、workdir 与
git root 配置。本模块只读 ``config.toml``；文件缺失、不可读或无法解析均按无冲突
处理，只有明确的同名条目会阻止创建。
"""

from __future__ import annotations

import logging
import os
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path

from trowel_py.codex_host.protocol import TROWEL_NOTE_SEARCH_SERVER_NAME

_log = logging.getLogger(__name__)

CONFIG_TOML = "config.toml"

# 限制 git root 探测时间，避免 session 创建被外部进程无限阻塞。
_GIT_TIMEOUT_S = 5.0


def resolve_codex_config_path(
    codex_home: str | os.PathLike[str] | None = None,
) -> Path:
    """解析 ``config.toml`` 路径：显式参数优先，其次 ``CODEX_HOME``，最后 ``~/.codex``。"""

    if codex_home is not None:
        home = Path(codex_home)
    else:
        env_home = os.environ.get("CODEX_HOME")
        home = Path(env_home) if env_home else Path.home() / ".codex"
    return home / CONFIG_TOML


@dataclass(frozen=True)
class McpConflict:
    """表示配置文件声明了正在检查的 MCP 服务名。

    Attributes:
        server_name: 在 ``mcp_servers`` 中找到的目标服务名。
        config_path: 声明该服务的 ``config.toml`` 路径。
    """

    server_name: str
    config_path: str


def find_conflicting_mcp_server(
    server_name: str = TROWEL_NOTE_SEARCH_SERVER_NAME,
    *,
    codex_home: str | os.PathLike[str] | None = None,
    workdir: str | os.PathLike[str] | None = None,
) -> McpConflict | None:
    """按 global、当前 workdir、git root 的顺序查找同名 MCP server。

    此函数无锁且每个文件只读取一次。文件缺失、权限错误等读取失败，以及并发写入
    产生的半成品 TOML，均按无冲突处理；下一次创建 session 时会重新读取。

    Args:
        server_name: 要检查的 MCP 服务名，默认检查 Trowel memory 服务。
        codex_home: Codex 配置目录覆盖；省略时先读取 ``CODEX_HOME``，环境变量也未
            设置时才使用 ``~/.codex``。
        workdir: 会话工作目录；None 表示不检查项目级配置。

    Returns:
        按检查顺序发现的首个冲突；没有明确同名条目时为 None。
    """

    for path in _collect_config_paths(codex_home, workdir):
        conflict = _check_one_layer(path, server_name)
        if conflict is not None:
            return conflict
    return None


def _collect_config_paths(
    codex_home: str | os.PathLike[str] | None,
    workdir: str | os.PathLike[str] | None,
) -> list[Path]:
    """收集当前检查覆盖的 global、workdir 与 git root 配置路径。

    项目层只检查 workdir 本身和 git root，不检查两者之间的祖先目录。结果按 global、
    workdir、git root 的报告顺序排列，并保留重复路径的首次出现位置。
    """

    paths = [resolve_codex_config_path(codex_home)]
    if workdir is not None:
        workdir_path = Path(workdir).resolve() / ".codex" / CONFIG_TOML
        paths.append(workdir_path)
        git_root = _git_root(workdir)
        if git_root is not None:
            git_path = git_root / ".codex" / CONFIG_TOML
            if git_path != workdir_path:
                paths.append(git_path)
    seen: set[Path] = set()
    unique: list[Path] = []
    for p in paths:
        if p not in seen:
            seen.add(p)
            unique.append(p)
    return unique


def _git_root(workdir: str | os.PathLike[str]) -> Path | None:
    """探测 git root；进程启动失败、超时、非零退出或空输出时返回 None。

    返回 None 只会省略 git root 配置层；调用方仍检查 global 和 workdir 配置。
    """

    try:
        result = subprocess.run(  # noqa: S603,S607 - 命令参数固定，cwd 不拼入 argv。
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(workdir),
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    root = result.stdout.strip()
    return Path(root) if root else None


def _check_one_layer(path: Path, server_name: str) -> McpConflict | None:
    """解析单个配置，并仅在 ``mcp_servers`` 含目标 key 时返回冲突。

    文件缺失、不可读、TOML 无效或 ``mcp_servers`` 不是表时均返回 None；读取与解析
    失败会写 warning，文件缺失不会。
    """

    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except FileNotFoundError:
        return None
    except (OSError, tomllib.TOMLDecodeError):
        _log.warning(
            "codex config at %s is unreadable; skipping MCP isolation check",
            path,
        )
        return None
    servers = data.get("mcp_servers")
    if not isinstance(servers, dict):
        return None
    if server_name in servers:
        return McpConflict(server_name=server_name, config_path=str(path))
    return None
