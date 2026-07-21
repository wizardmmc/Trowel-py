"""Self Manifest assembler (slice-085).

Builds a frozen :class:`SelfManifest` from runtime facts + M/P switches,
and renders it into the 套壳 (shell-on-top) injection text that is
prepended to the memory injection.

Layering (slice-085 spec §套壳而非替换): cc / codex already declare "you
help users with software engineering" and "you have tools / permissions"
in their default system prompt. This section does NOT repeat that — it
only adds the Trowel layer: you are invoked by Trowel, Trowel is a
continuous subject, this action is one episode of it, and here are
Trowel's subsystems with which are injected this call.

Cache friendliness (slice-085 invariant): the stable identity head
(identity / version / continuity note) is rendered as the verbatim prefix
of the section and never changes within a version, so a prompt-cache prefix
stays warm while the dynamic tail (本次调用 / 身体) shifts with
runtime / model / switches. No precise timestamp is embedded in the text.

M/P off non-leak (pass 5): the section only states the switch state
("未注入"); it never carries the memory root path, the search→read pointer
or any profile body — those belong to ``memory/injection.py``.
"""

from __future__ import annotations

import logging

from trowel_py.model_os.types import SelfManifest, SubsystemState

logger = logging.getLogger(__name__)

#: Stable identity. Bumped by humans on upgrade; never by the model.
IDENTITY = "Trowel"
VERSION = "trowel-os-v0"
CONTINUITY_NOTE = "本次行动是持续主体的一段活动"

#: Trowel-level subsystems that constitute its body schema. These are the
#: capabilities Trowel-as-a-system provides — NOT the native runtime's
#: tool/MCP roster (cc/codex carry those themselves). Pure product features
#: (garden / pet / cards / feynman) are deliberately excluded: the model
#: does not need to know about them to do its work.
TROWEL_SUBSYSTEMS: tuple[str, ...] = (
    "memory",
    "profile",
    "model_os",
    "dual_runtime",
    "todo_loop",
)

#: Human-readable labels for each subsystem, used in the 身体 (body) section.
#: Order follows TROWEL_SUBSYSTEMS.
_SUBSYSTEM_LABELS: dict[str, str] = {
    "memory": "记忆系统：跨会话记忆",
    "profile": "用户画像：合作者的画像",
    "model_os": "Model OS 内核：跨会话持续身份",
    "dual_runtime": "双 runtime：能调用 CC 和 Codex",
    "todo_loop": "todo loop：任务跟踪",
}

#: The reserved namespace marker for the Self section heading. Memory content
#: (diary / dictionary L0 — model-writable) must not carry this marker;
#: :func:`build_session_injection` watches for smuggling and warns. The prefix
#: form (no closing paren) matches regardless of the appended version suffix,
#: so the guard survives a version bump.
_SELF_NAMESPACE_MARKER = "# 关于你（Self"


def build_self_manifest(
    *,
    runtime: str,
    model: str | None,
    effort: str | None,
    memory_enabled: bool,
    profile_enabled: bool,
    permission_preset: str | None = None,
    task_id: str | None = None,
    episode_id: str | None = None,
    native_session_id: str | None = None,
) -> SelfManifest:
    """Assemble a :class:`SelfManifest` from runtime facts + switches.

    Args:
        runtime: which native host this session runs on ("cc" | "codex").
        model: effective model the host reported; ``None`` means unknown
            (host has not echoed yet). Never paper over None with a cache.
        effort: reasoning-effort override; ``None`` means unknown (cc has
            no machine echo per slice-083).
        memory_enabled: whether memory content is injected this call.
        profile_enabled: whether profile content is injected this call.
        permission_preset: runtime permission preset, or ``None``.
        task_id / episode_id / native_session_id: optional location
            pointers; left ``None`` when no Task/Episode is bound yet.

    Returns:
        A frozen :class:`SelfManifest`. Not persisted; the caller renders
        on demand via :func:`render_self_injection`.
    """

    return SelfManifest(
        identity=IDENTITY,
        version=VERSION,
        continuity_note=CONTINUITY_NOTE,
        runtime=runtime,
        model=model,
        effort=effort,
        subsystems=TROWEL_SUBSYSTEMS,
        memory_state=(
            SubsystemState.INJECTED if memory_enabled else SubsystemState.OFF
        ),
        profile_state=(
            SubsystemState.INJECTED if profile_enabled else SubsystemState.OFF
        ),
        native_tools_note=_native_tools_note(runtime),
        authorization_scope=_authorization_scope(permission_preset),
        task_id=task_id,
        episode_id=episode_id,
        native_session_id=native_session_id,
    )


def _native_tools_note(runtime: str) -> str:
    """Note that native tools/MCP come from the runtime, not duplicated here."""

    return f"本次 runtime 的工具与 MCP 由 {runtime} 自带，不在 trowel 层重复"


def _authorization_scope(permission_preset: str | None) -> str:
    """Render the authorization scope from the permission preset.

    v0 is a plain carry of the preset token; richer derivation (which
    concrete actions need approval) is left to slice-087 (Episode
    ownership / suspend).
    """

    if permission_preset is None:
        return "授权模式：未定"
    return f"授权模式：{permission_preset}"


def _state_line(state: SubsystemState) -> str:
    """Render a subsystem's injection state without leaking content."""

    if state == SubsystemState.INJECTED:
        return "本次内容已注入"
    return "本次内容未注入"


def render_self_injection(manifest: SelfManifest) -> str:
    """Render the Self section (套壳) prepended to the memory injection.

    Structure (cache-friendly):
    - **Stable head** (verbatim, never changes within a version): the
      ``# 关于你（Self）`` heading + identity + continuity note. Everything
      up to ``## 本次调用`` is static, so the prompt-cache prefix stays warm.
    - **Dynamic tail**: 本次调用 (runtime / model / native note / auth) and
      Trowel 的身体 (subsystem list with injection states).

    The model sees its continuous-subject framing, the Trowel body schema,
    and which subsystems are injected this call — without any memory root
    path or profile body leaking through (pass 5).
    """

    runtime = manifest.runtime
    model_line = manifest.model if manifest.model is not None else "未定"
    effort_line = manifest.effort if manifest.effort is not None else "未定"

    body_items: list[str] = []
    for subsystem in manifest.subsystems:
        label = _SUBSYSTEM_LABELS.get(subsystem, subsystem)
        if subsystem == "memory":
            state = _state_line(manifest.memory_state)
        elif subsystem == "profile":
            state = _state_line(manifest.profile_state)
        else:
            state = "常驻"
        body_items.append(f"- {label}（{state}）")
    body_text = "\n".join(body_items)

    return (
        f"# 关于你（Self · {manifest.version}）\n\n"
        f"你是 {manifest.identity} 的一段脑力活动。{manifest.continuity_note}。\n"
        f"{manifest.identity} 是跨会话持续存在的系统。\n\n"
        "## 本次调用\n"
        f"- runtime: {runtime}\n"
        f"- 你（{runtime} agent）是 {manifest.identity} 本次调用的一段脑力活动\n"
        f"- model: {model_line}\n"
        f"- effort: {effort_line}\n"
        f"- {manifest.native_tools_note}\n"
        f"- {manifest.authorization_scope}\n\n"
        "## Trowel 的身体\n"
        f"{body_text}\n\n"
        "## 合作\n"
        f"{manifest.identity} 正在跟一个具体的人合作。你在为这个人工作。"
    )


def build_session_injection(
    *,
    self_enabled: bool,
    memory_text: str,
    runtime: str,
    model: str | None,
    effort: str | None,
    memory_enabled: bool,
    profile_enabled: bool,
    permission_preset: str | None = None,
) -> str:
    """Build the full session injection: Self section prepended to memory text.

    This is the single compose point both spawn sites (``cc_host`` and the
    Codex hub path) call, so they produce identical ordering for identical
    switches (mirrors slice-078 C-4). The memory text is built by the caller
    via ``memory/injection.py`` and passed in — this module never imports
    memory, keeping ``model_os`` free of any ``memory`` dependency.

    Args:
        self_enabled: slice-085 switch. False drops the entire Self section
            (A/B baseline); ``memory_text`` is returned unchanged.
        memory_text: the already-built memory injection (may be ``""``).
        runtime: friendly runtime name ("cc" | "codex").
        model / effort / memory_enabled / profile_enabled / permission_preset:
            forwarded to :func:`build_self_manifest` for the dynamic tail.

    Returns:
        The composed injection text. Empty when ``self_enabled`` is False AND
        ``memory_text`` is empty — the caller maps ``""`` to ``None`` so the
        native host omits its injection flag entirely.
    """

    if not self_enabled:
        return memory_text
    manifest = build_self_manifest(
        runtime=runtime,
        model=model,
        effort=effort,
        memory_enabled=memory_enabled,
        profile_enabled=profile_enabled,
        permission_preset=permission_preset,
    )
    self_text = render_self_injection(manifest)
    # slice-085 anti-forgery guard: Memory content (diary / dictionary L0,
    # model-writable) must not smuggle a second Self section. Detect the
    # reserved namespace marker and warn; the canonical Self stays first so
    # the real identity wins the prefix. Memory-write-layer enforcement
    # (stripping the smuggled block at source) is deferred to a later slice.
    if _SELF_NAMESPACE_MARKER in memory_text:
        logger.warning(
            "memory injection carries the reserved Self namespace marker "
            "%r; a model may have smuggled Self-level text via Memory. "
            "Canonical Self stays first; memory-write-layer enforcement is "
            "deferred.",
            _SELF_NAMESPACE_MARKER,
        )
    parts = [part for part in (self_text, memory_text) if part]
    return "\n\n".join(parts)
