"""检测会破坏 memory-off 隔离的同名 MCP server。

``--disable memories`` 不会注销用户配置的 MCP server。同名配置仍可能在
memory-off thread 中启动，因此 hub 必须在创建 session 前检查 global、workdir 与
git root 配置。本模块只读 ``config.toml``；文件缺失或无法解析按无冲突处理，只有
明确的同名条目会阻止创建。
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
    codex_home: str | os.PathLike | None = None,
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
    server_name: str
    config_path: str


def find_conflicting_mcp_server(
    server_name: str = TROWEL_NOTE_SEARCH_SERVER_NAME,
    *,
    codex_home: str | os.PathLike | None = None,
    workdir: str | os.PathLike | None = None,
) -> McpConflict | None:
    """检查 global、当前 workdir 与 git root 配置中的同名 server。

    此函数无锁且只读取一次。并发写入产生的半成品 TOML 按无冲突处理，下一次创建
    session 时会重新读取；不能因用户正在编辑配置而阻塞本次创建。
    """

    for path in _collect_config_paths(codex_home, workdir):
        conflict = _check_one_layer(path, server_name)
        if conflict is not None:
            return conflict
    return None


def _collect_config_paths(
    codex_home: str | os.PathLike | None,
    workdir: str | os.PathLike | None,
) -> list[Path]:
    """收集当前检查覆盖的 global、workdir 与 git root 配置路径。

    Codex 上游 ``config/loader/mod.rs`` 还定义 tree layer；本函数当前只取端点路径，
    并按 global 优先的首次出现顺序去重。
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
    # 保留首次出现顺序，确保 global 配置优先报告。
    seen: set[Path] = set()
    unique: list[Path] = []
    for p in paths:
        if p not in seen:
            seen.add(p)
            unique.append(p)
    return unique


def _git_root(workdir: str | os.PathLike) -> Path | None:
    """返回 git root；非仓库、git 不可用或超时时均返回 ``None``。"""

    try:
        result = subprocess.run(  # noqa: S603,S607 - argv 受信，git 从 PATH 解析。
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
