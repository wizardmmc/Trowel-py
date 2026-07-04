"""Build the CC subprocess invocation.

Spec-fixed startup args (see slice022 + slice-025-c design constraints):
    claude -p --input-format stream-json --output-format stream-json --verbose
           --model <m> --fallback-model <m> --effort <level>
           --permission-mode <mode> --permission-prompt-tool stdio
           [--resume <cc_session_id>]

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

DEFAULT_MODEL = "glm-5.2"
DEFAULT_FALLBACK_MODEL = "glm-5.1"
DEFAULT_EFFORT = "medium"
DEFAULT_PERMISSION_MODE = "bypassPermissions"
DEFAULT_PERMISSION_PROMPT_TOOL = "stdio"

CLAUDE_BIN = shutil.which("claude") or "claude"


def build_args(
    workdir: str | os.PathLike,
    *,
    model: str = DEFAULT_MODEL,
    fallback_model: str = DEFAULT_FALLBACK_MODEL,
    effort: str = DEFAULT_EFFORT,
    permission_mode: str = DEFAULT_PERMISSION_MODE,
    permission_prompt_tool: str | None = DEFAULT_PERMISSION_PROMPT_TOOL,
    resume_from: str | None = None,
) -> list[str]:
    """Return the argv list for a CC subprocess. workdir is in the kwargs, not here."""
    args = [
        CLAUDE_BIN,
        "-p",
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
        "--verbose",
        "--model",
        model,
        "--fallback-model",
        fallback_model,
        "--effort",
        effort,
        "--permission-mode",
        permission_mode,
    ]
    if permission_prompt_tool:
        args += ["--permission-prompt-tool", permission_prompt_tool]
    if resume_from:
        args += ["--resume", resume_from]
    return args


def build_subprocess_kwargs(workdir: str | os.PathLike) -> dict[str, Any]:
    """Return asyncio.create_subprocess_exec kwargs for a CC subprocess.

    start_new_session=True gives each CC its own process group so the shutdown
    handler can SIGTERM/SIGKILL the whole group (no orphans).
    """
    return {
        "cwd": str(workdir),
        "stdin": asubprocess.PIPE,
        "stdout": asubprocess.PIPE,
        "stderr": asubprocess.PIPE,
        "start_new_session": True,
    }
