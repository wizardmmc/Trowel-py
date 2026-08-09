"""构造 CC 子进程的 argv 与 asyncio 启动参数。"""

from __future__ import annotations

import os
import shutil
from asyncio import subprocess as asubprocess
from collections.abc import Sequence
from typing import Any

# 缺省时省略 model、fallback_model 与 effort，让 CC 按自身配置解析。
DEFAULT_MODEL = None
DEFAULT_FALLBACK_MODEL = None
DEFAULT_EFFORT = None
DEFAULT_PERMISSION_MODE = "bypassPermissions"
DEFAULT_PERMISSION_PROMPT_TOOL = "stdio"

CLAUDE_BIN = shutil.which("claude") or "claude"


def build_args(
    workdir: str | os.PathLike[str],
    *,
    model: str | None = DEFAULT_MODEL,
    fallback_model: str | None = DEFAULT_FALLBACK_MODEL,
    effort: str | None = DEFAULT_EFFORT,
    permission_mode: str = DEFAULT_PERMISSION_MODE,
    permission_prompt_tool: str | None = DEFAULT_PERMISSION_PROMPT_TOOL,
    resume_from: str | None = None,
    append_system_prompt: str | None = None,
    mcp_config: str | None = None,
    allowed_tools: Sequence[str] | None = None,
    settings_path: str | os.PathLike[str] | None = None,
    setting_sources: str | None = None,
) -> list[str]:
    """构造 Claude Code stream-json 子进程的启动参数。

    Args:
        workdir: 子进程的工作目录；只由 ``cwd`` 承载，不进入参数列表。
        model: 本轮使用的模型；None 表示沿用 Claude Code 配置。
        fallback_model: 主模型不可用时的备用模型；None 表示不覆盖配置。
        effort: 模型思考强度；None 表示不覆盖配置。
        permission_mode: Claude Code 的权限模式。
        permission_prompt_tool: print 模式下处理权限交互的 MCP 工具名；None 或
            空字符串表示不注册。
        resume_from: 要恢复的 Claude Code 原生会话 ID；None 表示新会话。
        append_system_prompt: 追加到 Claude Code 默认系统提示词的 Trowel 上下文。
        mcp_config: 本会话独占的 MCP 配置文件；提供时同时启用 strict 模式。
        allowed_tools: 预先授权并向 Claude Code 声明需要发现的工具全名。
        settings_path: 当前会话独占的 Claude settings 文件。
        setting_sources: 允许 Claude 额外读取的配置来源；空字符串表示全部禁用。

    Returns:
        可直接传给 ``asyncio.create_subprocess_exec`` 的 argv。
    """

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
    # stdio 在 bypassPermissions 下保留交互请求通道；None 或空字符串表示不注册。
    if permission_prompt_tool:
        args += ["--permission-prompt-tool", permission_prompt_tool]
    if resume_from:
        args += ["--resume", resume_from]
    if append_system_prompt:
        args += ["--append-system-prompt", append_system_prompt]
    if allowed_tools:
        # 单个逗号分隔值避免 Commander 的可变参数吞掉后续选项。
        args += ["--allowedTools", ",".join(allowed_tools)]
    if settings_path is not None:
        args += ["--settings", str(settings_path)]
    if setting_sources is not None:
        args += ["--setting-sources", setting_sources]
    # strict 模式隔离项目、用户和插件中的额外 MCP 配置。
    if mcp_config:
        args += ["--mcp-config", mcp_config, "--strict-mcp-config"]
    return args


def build_subprocess_kwargs(
    workdir: str | os.PathLike[str],
    *,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """构造启动 Claude Code 子进程所需的关键字参数。

    标准输入、输出和错误都接入 asyncio 管道。子进程会创建独立的操作系统会话，
    并把 stream-json 单行读取上限设为 16 MiB。

    Args:
        workdir: 子进程使用的工作目录。
        env: 子进程使用的完整环境变量；None 表示省略 ``env`` 键并继承父进程
            环境，非 None 时不会自动与父环境合并。

    Returns:
        可直接传给 ``asyncio.create_subprocess_exec`` 的关键字参数。
    """

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
