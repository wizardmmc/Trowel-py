"""构造 CC 子进程的 argv 与 asyncio 启动参数。"""

from __future__ import annotations

import os
import shutil
from asyncio import subprocess as asubprocess
from typing import Any

# 缺省时省略 model、fallback_model 与 effort，让 CC 按自身配置解析。
DEFAULT_MODEL = None
DEFAULT_FALLBACK_MODEL = None
DEFAULT_EFFORT = None
DEFAULT_PERMISSION_MODE = "bypassPermissions"
DEFAULT_PERMISSION_PROMPT_TOOL = "stdio"

CLAUDE_BIN = shutil.which("claude") or "claude"


def build_args(
    workdir: str | os.PathLike,
    *,
    model: str | None = DEFAULT_MODEL,
    fallback_model: str | None = DEFAULT_FALLBACK_MODEL,
    effort: str | None = DEFAULT_EFFORT,
    permission_mode: str = DEFAULT_PERMISSION_MODE,
    permission_prompt_tool: str | None = DEFAULT_PERMISSION_PROMPT_TOOL,
    resume_from: str | None = None,
    append_system_prompt: str | None = None,
    mcp_config: str | None = None,
) -> list[str]:
    """构造 CC argv；workdir 只由子进程 cwd 承载，不进入 argv。"""
    args = [
        CLAUDE_BIN,
        "-p",
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
        "--verbose",
        "--permission-mode",
        permission_mode,
    ]
    if model is not None:
        args += ["--model", model]
    if fallback_model is not None:
        args += ["--fallback-model", fallback_model]
    if effort is not None:
        args += ["--effort", effort]
    # stdio 在 bypassPermissions 下保留交互请求通道；None 表示不注册。
    if permission_prompt_tool:
        args += ["--permission-prompt-tool", permission_prompt_tool]
    if resume_from:
        args += ["--resume", resume_from]
    if append_system_prompt:
        args += ["--append-system-prompt", append_system_prompt]
    # strict 模式隔离项目、用户和插件中的额外 MCP 配置。
    if mcp_config:
        args += ["--mcp-config", mcp_config, "--strict-mcp-config"]
    return args


def build_subprocess_kwargs(
    workdir: str | os.PathLike,
    *,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """构造子进程参数；env=None 时省略该键，使子进程继承父环境。"""
    kwargs: dict[str, Any] = {
        "cwd": str(workdir),
        "stdin": asubprocess.PIPE,
        "stdout": asubprocess.PIPE,
        "stderr": asubprocess.PIPE,
        # 独立进程组供 interrupt 向整组发送 SIGINT。
        "start_new_session": True,
        # 单条 stream-json 可能超过 asyncio 默认的 64 KiB 上限。
        "limit": 16 * 1024 * 1024,
    }
    if env is not None:
        kwargs["env"] = env
    return kwargs
