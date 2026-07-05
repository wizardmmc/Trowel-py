"""Input-side slash handling for the CC host.

CC's stream-json mode has no command input channel (probe-verified). So trowel
intercepts any `/<name>` text itself, classifies it into one of several
actions, and feeds CC only plain text. This is what makes `/skillname` and
custom commands work — and what keeps `/cost` `/effort` etc. as trowel-level
controls rather than confusing the model.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# Built-in slash commands handled by trowel, never passed through.
_LOCAL_COMMANDS = frozenset({"cost", "status"})
_RESTART_COMMANDS = frozenset({"effort", "model"})
_UNSUPPORTED = frozenset({"compress"})
# slice-028 bug3: /exit (alias /quit) — CC's stream-json mode does NOT intercept
# the literal "/exit" string (it's a `local-jsx` command in the interactive TUI
# only, never dispatched in print/stream-json mode). The host shuts CC down via
# the stream-json control_request(subtype=end_session) channel instead (the
# canonical non-interactive shutdown path in cc's cli/print.ts). See
# service.CCHost._exit_session.
_EXIT_COMMANDS = frozenset({"exit", "quit"})


# ---------------------------------------------------------------------------
# Action result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SendText:
    """Send this plain text to CC as a user message."""

    text: str


@dataclass(frozen=True)
class LocalCommand:
    """trowel answers itself from accumulated data (no CC round-trip)."""

    kind: str  # "cost" | "status"


@dataclass(frozen=True)
class RestartSession:
    """Seamless restart of the session with new --effort / --model (history kept)."""

    effort: str | None = None
    model: str | None = None


@dataclass(frozen=True)
class UnsupportedSlash:
    """A slash command v1 cannot fulfill (e.g. /compress under headless mode)."""

    name: str
    message: str = "this command is not supported in stream-json mode"


@dataclass(frozen=True)
class ExitSession:
    """User requested session exit (/exit, /quit).

    CC's stream-json mode does NOT intercept the literal "/exit" string (a
    `local-jsx` command in the interactive TUI only — never dispatched in
    print/stream-json mode; sending it as text just confuses the model). The
    host handles it directly: shut CC down via the stream-json
    control_request(subtype=end_session) channel, which is the canonical
    non-interactive shutdown path (cc emits its final `result` event and
    exits 0). See service.CCHost._exit_session.
    """


InputAction = SendText | LocalCommand | RestartSession | UnsupportedSlash | ExitSession


# ---------------------------------------------------------------------------
# Helpers (overridable for tests)
# ---------------------------------------------------------------------------


def user_commands_dir() -> Path:
    """Return the user-level ~/.claude/commands directory."""
    return Path.home() / ".claude" / "commands"


def project_commands_dir(workdir: str | os.PathLike) -> Path:
    """Return the project-level <workdir>/.claude/commands directory."""
    return Path(workdir) / ".claude" / "commands"


def skill_trigger_prompt(name: str, args: str) -> str:
    """Build the prompt that makes the model actually call the Skill tool.

    Smoke B proved this shape works: the model issues tool_use(Skill,
    input={'skill': name}). Args are appended so they reach the forked skill.
    """
    base = f"Use the Skill tool with skill='{name}'."
    return f"{base} {args}".strip()


def expand_command_file(md_path: Path, args: str) -> str:
    """Strip frontmatter and substitute $ARGUMENTS in a custom command md."""
    raw = md_path.read_text(encoding="utf-8")
    parts = raw.split("---", 2)
    body = parts[-1].strip() if len(parts) >= 3 else raw.strip()
    return body.replace("$ARGUMENTS", args)


def _find_command_file(name: str, workdir: str | os.PathLike) -> Path | None:
    """Look up a custom command md, project dir first then user dir."""
    candidates = [
        project_commands_dir(workdir) / f"{name}.md",
        user_commands_dir() / f"{name}.md",
    ]
    for c in candidates:
        if c.is_file():
            return c
    return None


def _split_command(text: str) -> tuple[str, str] | None:
    """Split `/name args` into (name, args). Returns None if not a command."""
    s = text.strip()
    if not s.startswith("/") or len(s) < 2 or s.startswith("//"):
        return None
    rest = s[1:]
    parts = rest.split(None, 1)
    name = parts[0]
    args = parts[1].strip() if len(parts) > 1 else ""
    return name, args


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def classify_input(text: str, workdir: str | os.PathLike) -> InputAction:
    """Classify a user input line into an action.

    Priority: builtin (cost/status/effort/model/compress) > custom command
    file > generic skill trigger. Plain text (no leading `/`) passes through.
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
