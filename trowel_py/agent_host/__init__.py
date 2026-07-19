"""trowel_py.agent_host — host-neutral Session Hub (slice-072, M9).

The Session Hub lifts trowel's CC-only session registry into a host-neutral
binding layer so Claude Code and Codex sessions coexist in one workspace.
This package owns:

* :class:`SessionBinding` / :class:`Runtime` — the frozen public shape of a
  session (which runtime owns it + the native session id).
* :class:`BindingStore` — JSON-backed persistence so bindings survive a
  trowel restart.
* :class:`SessionHub` — the in-memory coordinator that routes create /
  send / interrupt / resume to the right native host (CCHost or the shared
  CodexHostManager), enforces the runtime-frozen and no-cross-resume
  invariants, and mixes the two runtimes in one active/history list.

The legacy ``/api/cc/*`` routes stay untouched (spec C-5: progressive
compatibility — no repo-wide rename). The new ``/api/agent/*`` routes are the
host-neutral entry points; CC sessions created there reuse cc_host's CCHost
so the live ``_REGISTRY`` stays the single CC store.
"""

from __future__ import annotations

from trowel_py.agent_host.binding import (
    Runtime,
    SessionBinding,
    binding_from_dict,
    make_binding,
)
from trowel_py.agent_host.hub import (
    CC_CAPABILITIES,
    CODEX_CAPABILITIES,
    CrossRuntimeResumeError,
    MAX_CONNECTIONS,
    MAX_RUNNING,
    RuntimeFrozenError,
    SessionHub,
)
from trowel_py.agent_host.store import BindingStore, resolve_bindings_path

__all__ = [
    "BindingStore",
    "CC_CAPABILITIES",
    "CODEX_CAPABILITIES",
    "CrossRuntimeResumeError",
    "MAX_CONNECTIONS",
    "MAX_RUNNING",
    "Runtime",
    "RuntimeFrozenError",
    "SessionBinding",
    "SessionHub",
    "binding_from_dict",
    "make_binding",
    "resolve_bindings_path",
]
