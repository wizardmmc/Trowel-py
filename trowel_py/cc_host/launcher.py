"""Build the CC subprocess invocation.

Spec-fixed startup args (see slice022 + slice-025-c design constraints):
    claude -p --input-format stream-json --output-format stream-json --verbose
           --permission-mode <mode> --permission-prompt-tool stdio
           [--model <m>] [--fallback-model <m>] [--effort <level>]
           [--resume <cc_session_id>]

slice-027: --model / --fallback-model / --effort are OPTIONAL. They are omitted
unless the user runs /model or /effort (RestartSession), so by default CC reads
~/.claude/settings.json (ANTHROPIC_MODEL env > settings.model). The old hardcoded
glm-5.2 / glm-5.1 / medium were placeholders.

The workdir is NOT an arg — it is the subprocess cwd (so CC loads that
project's .claude/). `--add-dir` is deliberately unused.

`--permission-prompt-tool stdio` (slice-025-c, default-on) opens the ask
channel under bypassPermissions: ordinary tools stay silent (bypass auto-allow
via permissions.ts 2a), only requiresUserInteraction tools (AskUserQuestion /
EnterPlanMode / ExitPlanMode) reach us as control_request. Ground truth:
reverse_cc samples/raw/052_askuser_bypass_stdio.jsonl. Pass None to restore
the pre-025-c behavior (no interactive tool channel).
"""

from __future__ import annotations

import os
import shutil
from asyncio import subprocess as asubprocess
from typing import Any

# slice-027: all None — when not overridden by /model or /effort (RestartSession),
# launcher OMITS the flag and CC reads ~/.claude/settings.json itself
# (ANTHROPIC_MODEL env > settings.model). The old glm-5.2 / glm-5.1 / medium
# were only placeholders; trowel now defers to the user's cc config so changing
# backends only requires editing settings.json, not trowel code.
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
) -> list[str]:
    """Return the argv list for a CC subprocess. workdir is in the kwargs, not here.

    slice-027: model / fallback_model / effort default to None — when not
    overridden (i.e. the user has not run /model or /effort this session), the
    flag is omitted entirely so CC resolves the value from ~/.claude/settings.json
    (ANTHROPIC_MODEL env > settings.model). Passing a value (e.g. /model opus →
    RestartSession) adds the flag back and overrides CC's config for this session.
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
    if permission_prompt_tool:
        args += ["--permission-prompt-tool", permission_prompt_tool]
    if resume_from:
        args += ["--resume", resume_from]
    return args


def build_subprocess_kwargs(
    workdir: str | os.PathLike,
    *,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Return asyncio.create_subprocess_exec kwargs for a CC subprocess.

    start_new_session=True gives each CC its own process group so the shutdown
    handler can SIGTERM/SIGKILL the whole group (no orphans).

    slice-028 bug1: limit=16MB raises asyncio's StreamReader readline cap from
    the default 64KB. CC can emit a single stream-json line > 64KB (large
    tool_result / big file read); the default cap made readline raise
    ``ValueError: Separator is found, but chunk is longer than limit`` which
    bubbled up as host_error and killed the turn. 16MB covers the largest line
    seen in practice (slice-027 spiked at 1.08MB). service.send() still has a
    defensive try/except for anything beyond this.

    slice-030: ``env`` (keyword-only) is the pre-built subprocess env — the
    proxy delta (CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST + provider vars, with
    ANTHROPIC_BASE_URL pointed at the local reverse proxy) merged into
    os.environ by CCHost. None (default) means inherit the parent env, which
    is the pre-030 behavior. Keyword-only so workdir stays the only positional.

    Args:
        workdir: the subprocess cwd (CC loads that project's .claude/).
        env: full env dict for the subprocess, or None to inherit the parent
            env (``create_subprocess_exec`` default).

    Returns:
        kwargs ready for ``asyncio.create_subprocess_exec(**kwargs)``.
    """
    kwargs: dict[str, Any] = {
        "cwd": str(workdir),
        "stdin": asubprocess.PIPE,
        "stdout": asubprocess.PIPE,
        "stderr": asubprocess.PIPE,
        "start_new_session": True,
        # slice-028 bug1: 16MB readline cap (asyncio StreamReader `limit`)
        "limit": 16 * 1024 * 1024,
    }
    if env is not None:
        kwargs["env"] = env
    return kwargs
