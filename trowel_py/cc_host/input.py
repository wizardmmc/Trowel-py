"""把 CC Host 收到的文本分类为发送内容或本地控制动作。

上游 stream-json 模式没有交互命令输入通道，因此 host 必须先分类 ``/<name>``，
只向 Claude Code 发送普通文本。``/exit`` 和 ``/quit`` 由 service 通过
``end_session`` 控制通道执行，不能作为文本发送。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

_LOCAL_COMMANDS = frozenset({"cost", "status"})
_RESTART_COMMANDS = frozenset({"effort", "model"})
_UNSUPPORTED = frozenset({"compress"})
_EXIT_COMMANDS = frozenset({"exit", "quit"})


@dataclass(frozen=True)
class SendText:
    """表示可以直接发送给 Claude Code 的文本。

    Attributes:
        text: 用户原文、展开后的 command 正文或 Skill tool 触发文本。
    """

    text: str


@dataclass(frozen=True)
class LocalCommand:
    """表示由 Trowel 本地执行且不发送给 Claude Code 的查询命令。

    Attributes:
        kind: 本地命令名，当前为 ``cost`` 或 ``status``。
    """

    kind: str


@dataclass(frozen=True)
class RestartSession:
    """表示需要重启 Claude Code 会话才能生效的配置变更。

    Attributes:
        effort: ``/effort`` 指定的 reasoning effort；命令未提供值或修改模型时为
            None。
        model: ``/model`` 指定的模型名；命令未提供值或修改 effort 时为 None。
    """

    effort: str | None = None
    model: str | None = None


@dataclass(frozen=True)
class UnsupportedSlash:
    """表示 stream-json 模式不支持的 slash 命令。

    Attributes:
        name: 不受支持的命令名，不含开头的 ``/``。
        message: 返回给调用方的固定英文错误说明。
    """

    name: str
    message: str = "this command is not supported in stream-json mode"


@dataclass(frozen=True)
class ExitSession:
    """表示应由 service 通过 ``end_session`` 控制通道结束会话。"""


InputAction = SendText | LocalCommand | RestartSession | UnsupportedSlash | ExitSession


def user_commands_dir() -> Path:
    """返回当前用户的 Claude Code command 目录。"""

    return Path.home() / ".claude" / "commands"


def project_commands_dir(workdir: str | os.PathLike) -> Path:
    """返回指定工作目录下的项目级 Claude Code command 目录。

    Args:
        workdir: Claude Code 会话运行所在的工作目录。

    Returns:
        ``<workdir>/.claude/commands`` 路径。
    """

    return Path(workdir) / ".claude" / "commands"


def skill_trigger_prompt(name: str, args: str) -> str:
    """构造能触发 Skill tool 并透传参数的普通文本。

    Args:
        name: 要调用的 skill 名，可包含 plugin 命名空间。
        args: 用户在 slash 命令名之后输入的参数。

    Returns:
        发送给 Claude Code 的 Skill tool 触发文本。
    """

    base = f"Use the Skill tool with skill='{name}'."
    return f"{base} {args}".strip()


def expand_command_file(md_path: Path, args: str) -> str:
    """读取 command 文件，去除 frontmatter 并替换全部 ``$ARGUMENTS``。

    按前两个 ``---`` 拆分文件；存在两个分隔符时只使用第二个分隔符之后的内容，
    否则使用完整文件。

    Args:
        md_path: Claude Code command Markdown 文件的路径。
        args: 用于替换每个 ``$ARGUMENTS`` 占位符的用户参数。

    Returns:
        先去除首尾空白、再替换全部占位符的 command 正文。

    Raises:
        OSError: command 文件无法读取。
        UnicodeDecodeError: command 文件不是有效的 UTF-8 文本。
    """

    raw = md_path.read_text(encoding="utf-8")
    parts = raw.split("---", 2)
    body = parts[-1].strip() if len(parts) >= 3 else raw.strip()
    return body.replace("$ARGUMENTS", args)


def _find_command_file(name: str, workdir: str | os.PathLike) -> Path | None:
    """按项目级、用户级顺序查找同名 command 文件。

    Args:
        name: command 名，不含开头的 ``/`` 和 ``.md`` 后缀。
        workdir: 用于定位项目级 command 目录的工作目录。

    Returns:
        第一个存在的 command 文件；两个位置都没有该文件时为 None。
    """

    candidates = [
        project_commands_dir(workdir) / f"{name}.md",
        user_commands_dir() / f"{name}.md",
    ]
    for c in candidates:
        if c.is_file():
            return c
    return None


def _split_command(text: str) -> tuple[str, str] | None:
    """从单斜杠开头的输入中拆出命令名和参数。

    Args:
        text: 用户提交的原始文本。

    Returns:
        去除外层空白后的命令名和参数；输入不是 slash 命令、只有 ``/`` 或以
        ``//`` 开头时为 None。
    """

    s = text.strip()
    if not s.startswith("/") or len(s) < 2 or s.startswith("//"):
        return None
    rest = s[1:]
    parts = rest.split(None, 1)
    name = parts[0]
    args = parts[1].strip() if len(parts) > 1 else ""
    return name, args


def classify_input(text: str, workdir: str | os.PathLike) -> InputAction:
    """按内置命令、command 文件和 Skill prompt 的顺序分类输入。

    内置命令优先于同名 command 文件；项目级 command 优先于用户级 command。
    找不到 command 文件时，将 slash 命令转换为 Skill tool 触发文本。单独的
    ``/``、以 ``//`` 开头的输入和普通文本均保持原文。

    Args:
        text: 用户提交的原始输入。
        workdir: 当前会话的工作目录，用于查找项目级 command 文件。

    Returns:
        service 应执行的输入动作。

    Raises:
        OSError: 找到的 command 文件无法读取。
        UnicodeDecodeError: 找到的 command 文件不是有效的 UTF-8 文本。
    """

    split = _split_command(text)
    if split is None:
        return SendText(text=text)
    name, args = split

    if name in _LOCAL_COMMANDS:
        return LocalCommand(kind=name)
    if name in _RESTART_COMMANDS:
        if name == "effort":
            return RestartSession(effort=args or None)
        return RestartSession(model=args or None)
    if name in _UNSUPPORTED:
        return UnsupportedSlash(name=name)
    if name in _EXIT_COMMANDS:
        return ExitSession()

    cmd_file = _find_command_file(name, workdir)
    if cmd_file is not None:
        return SendText(text=expand_command_file(cmd_file, args))

    return SendText(text=skill_trigger_prompt(name, args))
