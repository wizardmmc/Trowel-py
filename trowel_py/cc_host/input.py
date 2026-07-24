"""处理 CC Host 输入侧的 slash 命令。

上游 stream-json 模式没有交互命令输入通道，因此 host 必须先分类 `/<name>`，
只向 CC 发送普通文本。`/exit` 和 `/quit` 由 service 通过 `end_session` 控制通道
执行，不能作为文本发送。
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
    text: str


@dataclass(frozen=True)
class LocalCommand:
    kind: str


@dataclass(frozen=True)
class RestartSession:
    effort: str | None = None
    model: str | None = None


@dataclass(frozen=True)
class UnsupportedSlash:
    name: str
    message: str = "this command is not supported in stream-json mode"


@dataclass(frozen=True)
class ExitSession:
    """交给 service 的 end_session 控制通道，不能发送为普通文本。"""


InputAction = SendText | LocalCommand | RestartSession | UnsupportedSlash | ExitSession


def user_commands_dir() -> Path:
    return Path.home() / ".claude" / "commands"


def project_commands_dir(workdir: str | os.PathLike) -> Path:
    return Path(workdir) / ".claude" / "commands"


def skill_trigger_prompt(name: str, args: str) -> str:
    """构造已验证能触发 Skill tool 且透传参数的普通文本。"""
    base = f"Use the Skill tool with skill='{name}'."
    return f"{base} {args}".strip()


def expand_command_file(md_path: Path, args: str) -> str:
    """去除 command frontmatter 并替换 `$ARGUMENTS`。"""
    raw = md_path.read_text(encoding="utf-8")
    parts = raw.split("---", 2)
    body = parts[-1].strip() if len(parts) >= 3 else raw.strip()
    return body.replace("$ARGUMENTS", args)


def _find_command_file(name: str, workdir: str | os.PathLike) -> Path | None:
    """优先查找项目 command，再查找用户 command。"""
    candidates = [
        project_commands_dir(workdir) / f"{name}.md",
        user_commands_dir() / f"{name}.md",
    ]
    for c in candidates:
        if c.is_file():
            return c
    return None


def _split_command(text: str) -> tuple[str, str] | None:
    s = text.strip()
    if not s.startswith("/") or len(s) < 2 or s.startswith("//"):
        return None
    rest = s[1:]
    parts = rest.split(None, 1)
    name = parts[0]
    args = parts[1].strip() if len(parts) > 1 else ""
    return name, args


def classify_input(text: str, workdir: str | os.PathLike) -> InputAction:
    """按 builtin、command 文件、generic skill 的顺序分类输入。"""
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
