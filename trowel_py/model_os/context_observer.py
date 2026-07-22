"""Production Context Observer (slice-088).

Turns a stream of CC / Codex runtime events into one trustworthy "how full is
the main session right now" number per active Episode. Untrusted → ``unavailable``,
never a fabricated 0%.

This module is deliberately pure: events in, :class:`ContextSample` values out.
No I/O, no globals. The journal write lives in :mod:`trowel_py.model_os.store`,
the reducer projection in :mod:`trowel_py.model_os.reducer`, and the runtime
wiring in :mod:`trowel_py.agent_host`.

The CC algorithm is ported item-by-item from the slice-082 spike
(``spikes/context-metering-20260721/context_sample.py``); the spike's golden
fixtures are promoted to ``tests/model_os/fixtures/context_observer/cc/`` and
the production calculator MUST reproduce their evidence numbers. The Codex
algorithm follows the slice-083 contract
(``spikes/runtime-signals-20260721/runtime-signal-contract.md`` §Codex 0.144.0).

Revisions after the slice-088 codex review (``docs/slices/activate/slice-088-codex-review.md``):

- **A2**: a real-model assistant event whose ``usage`` is missing still yields
  an ``unavailable`` sample (it is an observation that something was redacted
  / lost), it is NOT silently skipped. Only ``<synthetic>`` slash-command
  output is skipped — that is not a model call and carries no context占用.
- **M4**: the effective window is resolved per-sample from that sample's
  ``model`` (resume can switch model on the same session — 083). The spike
  cached the first model's window; the production observer must not.
- **M3**: every sample carries ``source_version`` (CC 2.1.197 / Codex 0.144.0)
  so a future ``UNKNOWN_VERSION_SHAPE`` degradation is auditable.
- **A5**: a Codex compact boundary is only consumed on
  ``item/completed(type=contextCompaction)``; ``thread/compact/start`` returning
  ``{}`` does NOT start a new generation.
- **M1**: a Codex usage event with no ``turnId`` cannot form a stable
  ``request_identity``; it degrades to ``unavailable`` rather than collapsing
  many samples onto one ``...None...`` event id.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Literal, Mapping, Sequence

# Mirrors CC's src/utils/context.ts:9 MODEL_CONTEXT_WINDOW_DEFAULT. All real CC
# models default to 200k; the GLM backend ignores the ``[1m]`` suffix (env #46416)
# so glm-5.x max is 200k. Sourced from slice-082.
DEFAULT_CONTEXT_WINDOW = 200_000

# CC src/utils/tokens.ts:12 — slash-command local output is disguised as an
# assistant message with model ``<synthetic>``; it carries no model usage and
# must be skipped (it is not a model call, so it contributes no context占用).
SYNTHETIC_MODEL = "<synthetic>"

MainOrSubagent = Literal["main", "subagent"]


class ContextConfidence(str, Enum):
    """Trust level of one observation, aligned to the slice-083 signal matrix.

    ``RELIABLE``: a machine observation grounded in a real runtime field.
    ``WEAK``: present but not decision-grade on its own (reserved for future
    model-self-report signals).
    ``UNAVAILABLE``: no trustworthy number; callers MUST treat ``ratio`` /
    ``used_tokens`` as ``None`` and never as 0%.
    """

    RELIABLE = "reliable"
    WEAK = "weak"
    UNAVAILABLE = "unavailable"


class UnavailableReason(str, Enum):
    """Why a sample is ``UNAVAILABLE``. ``NONE`` means the sample is trusted.

    Kept as an enum (not a free string) so the UI can render a specific reason
    ("context占用不可用：模型版本未知") instead of a bare "unavailable".
    """

    NONE = "none"
    UNKNOWN_MODEL = "unknown_model"
    UNKNOWN_WINDOW = "unknown_window"
    MISSING_USAGE = "missing_usage"
    REDACTED_USAGE = "redacted_usage"
    UNKNOWN_VERSION_SHAPE = "unknown_version_shape"
    MISSING_REQUEST_IDENTITY = "missing_request_identity"


# Map of canonical model name fragments → real context window. Sourced from
# CC src/utils/context.ts getModelCapability + the GLM backend note. GLM-5.x
# reports 200k regardless of the ``[1m]`` suffix.
_KNOWN_WINDOWS: Mapping[str, int] = {
    "glm-5": DEFAULT_CONTEXT_WINDOW,
    "glm-4": DEFAULT_CONTEXT_WINDOW,
    "claude-sonnet-4": DEFAULT_CONTEXT_WINDOW,
    "claude-opus-4": DEFAULT_CONTEXT_WINDOW,
    "claude-haiku-4": DEFAULT_CONTEXT_WINDOW,
}


def resolve_window(model: str | None) -> int | None:
    """Resolve the trusted effective context window for a model string.

    Args:
        model: the model string from an assistant message (e.g. ``glm-5.2``,
            ``glm-5.2[1m]``, ``<synthetic>``). ``None`` if unknown.

    Returns:
        The window in tokens, or ``None`` when the model is unknown / synthetic
        / untrusted. ``None`` propagates to ``ContextSample.ratio = None`` and
        ``confidence = UNAVAILABLE`` — never to a fake 0%.
    """

    if not model or model == SYNTHETIC_MODEL:
        return None
    lower = model.lower().replace("[1m]", "").strip()
    for prefix, window in _KNOWN_WINDOWS.items():
        if lower.startswith(prefix):
            return window
    return None


@dataclass(frozen=True)
class ContextSample:
    """One observation of the main (or subagent) session's context占用.

    Token fields are ``Optional``: they are ``None`` when the underlying usage
    could not be read (A2). ``ratio`` / ``used_tokens`` are ``None`` whenever
    the observation is ``UNAVAILABLE``; callers MUST treat ``None`` as
    "unavailable", never as 0%.

    Ownership (``task_id`` / ``episode_id``) and the event time are NOT
    duplicated here — they live on the :class:`~trowel_py.model_os.types.EventEnvelope`
    as the single source of truth (codex review extra-HIGH 2). This dataclass
    is the structured payload + the value returned by a latest-sample query.

    ``source_version`` is an audit field (which runtime version produced this
    sample, e.g. "2.1.197" / "0.144.0"). It does NOT drive ``confidence`` —
    confidence is driven by shape + window (the signals 082/083 actually
    evidence). ``UNKNOWN_VERSION_SHAPE`` is reserved for a future runtime that
    reports a version whose event shape this calculator cannot yet parse.
    """

    native_session_id: str
    main_or_subagent: MainOrSubagent
    turn_id: str | None
    request_identity: str
    generation: int
    input_tokens: int | None
    cache_creation_input_tokens: int | None
    cache_read_input_tokens: int | None
    output_tokens: int | None
    used_tokens: int | None
    effective_window_tokens: int | None
    ratio: float | None
    source: str
    source_version: str | None
    confidence: ContextConfidence
    unavailable_reason: UnavailableReason


# ---------------------------------------------------------------------------
# CC — normalized event + calculator (ported from slice-082).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NormalizedCcEvent:
    """The bits of a CC event the metering algorithms need.

    Extracted from a raw CC jsonl row or a tcc-streamed envelope by the caller
    (runtime wiring). Carrying a normalized event here keeps the calculator
    independent of the on-disk / wire format. Shape is identical to the
    slice-082 spike's ``NormalizedEvent`` so the golden fixtures port directly.
    """

    type: str
    subtype: str | None
    timestamp: str | None
    message_id: str | None
    model: str | None
    usage: Mapping[str, int] | None
    turn_id: str | None
    # Only on compact_boundary rows: ``manual`` / ``auto`` / None.
    compact_trigger: str | None = None
    compact_pre_tokens: int | None = None
    compact_post_tokens: int | None = None


def _is_compact_boundary(ev: NormalizedCcEvent) -> bool:
    return ev.type == "system" and ev.subtype == "compact_boundary"


def _is_synthetic_assistant(ev: NormalizedCcEvent) -> bool:
    """A slash-command local-output row disguised as assistant. Not a model
    call — skipped entirely (contributes no context占用)."""

    return ev.type == "assistant" and ev.model == SYNTHETIC_MODEL


def _as_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return 0


def _cc_sample(
    ev: NormalizedCcEvent,
    *,
    generation: int,
    native_session_id: str,
    main_or_subagent: MainOrSubagent,
    source_version: str | None,
    window: int | None,
    usage: Mapping[str, int] | None,
) -> ContextSample:
    """Build one ContextSample from a single CC assistant event.

    Three outcomes (A2):
    - real usage present + window known  → RELIABLE sample with ratio.
    - real usage present + window unknown → UNAVAILABLE (UNKNOWN_WINDOW); the
      token counts are still recorded so a human can see the raw numbers.
    - usage missing on a real-model event → UNAVAILABLE (REDACTED_USAGE if the
      whole usage block is absent, MISSING_USAGE if a dict lacks input_tokens);
      all token fields are ``None``.
    """

    base = dict(
        native_session_id=native_session_id,
        main_or_subagent=main_or_subagent,
        turn_id=ev.turn_id,
        request_identity=ev.message_id or "(no-id)",
        generation=generation,
        source="cc",
        source_version=source_version,
        effective_window_tokens=window,
    )
    has_usage = isinstance(usage, Mapping) and "input_tokens" in usage
    if has_usage:
        inp = _as_int(usage.get("input_tokens"))  # type: ignore[union-attr]
        cc = _as_int(usage.get("cache_creation_input_tokens"))  # type: ignore[union-attr]
        cr = _as_int(usage.get("cache_read_input_tokens"))  # type: ignore[union-attr]
        out = _as_int(usage.get("output_tokens"))  # type: ignore[union-attr]
        # CC getTokenCountFromUsage (tokens.ts:55): context size at this API
        # call = all input + cache + output (post-response占用).
        used = inp + cc + cr + out
        if window is None or window <= 0:
            return ContextSample(
                **base,
                input_tokens=inp,
                cache_creation_input_tokens=cc,
                cache_read_input_tokens=cr,
                output_tokens=out,
                used_tokens=used,
                ratio=None,
                confidence=ContextConfidence.UNAVAILABLE,
                unavailable_reason=UnavailableReason.UNKNOWN_WINDOW,
            )
        return ContextSample(
            **base,
            input_tokens=inp,
            cache_creation_input_tokens=cc,
            cache_read_input_tokens=cr,
            output_tokens=out,
            used_tokens=used,
            ratio=round(used / window, 4),
            confidence=ContextConfidence.RELIABLE,
            unavailable_reason=UnavailableReason.NONE,
        )
    # Real-model assistant with no readable usage — still an observation.
    reason = (
        UnavailableReason.REDACTED_USAGE if usage is None else UnavailableReason.MISSING_USAGE
    )
    return ContextSample(
        **base,
        input_tokens=None,
        cache_creation_input_tokens=None,
        cache_read_input_tokens=None,
        output_tokens=None,
        used_tokens=None,
        ratio=None,
        confidence=ContextConfidence.UNAVAILABLE,
        unavailable_reason=reason,
    )


def extract_cc_samples(
    events: Sequence[NormalizedCcEvent],
    *,
    native_session_id: str,
    main_or_subagent: MainOrSubagent,
    source_version: str | None = None,
    window_resolver=resolve_window,
) -> list[ContextSample]:
    """Walk a CC event stream and emit one ContextSample per deduplicated
    assistant message.

    Implements the slice-082 algorithms:

    - **Dedup (alg 2)**: CC does not deduplicate by ``message.id`` itself;
      ``getCurrentUsage`` walks back to the last real usage. We dedup only so
      a historical sample stream is not full of repeats, and the rule is
      "same id → keep the LAST record" — verified on real GLM-5.2 data where a
      subagent transcript's same ``message.id`` evolves from ``{0,0}`` to the
      real usage (taking the first would freeze a bogus 0).
    - **Generation (alg 3)**: each ``system:compact_boundary`` starts a new
      generation; the sample's ratio uses the post-boundary window, never the
      pre-compact peak.
    - **Window (alg 4, revised M4)**: resolved per-sample from that sample's
      ``model`` (not cached from the first model) so a resume that switches
      model on the same session is honoured.

    Args:
        events: normalized CC events from one session, in order.
        native_session_id: the CC session id this stream belongs to.
        main_or_subagent: tags every sample; subagent samples NEVER feed the
            main ratio (082 C-3).
        source_version: CC version that produced these events (e.g. "2.1.197").
        window_resolver: injected so tests can force a window or ``None``
            (unavailable) without monkey-patching.

    Returns:
        Deduplicated ContextSamples in event order. Empty only if no real-model
        assistant event ever appeared.
    """

    # Phase 1: walk once; for each message.id keep the LAST real-assistant
    # record (overwriting earlier siblings). No-id rows are kept standalone.
    latest_by_id: dict[str, tuple[int, int, NormalizedCcEvent]] = {}
    no_id: list[tuple[int, int, NormalizedCcEvent]] = []
    generation = 0
    for idx, ev in enumerate(events):
        if _is_compact_boundary(ev):
            generation += 1
            continue
        if _is_synthetic_assistant(ev):
            continue
        if ev.type != "assistant":
            continue
        record = (idx, generation, ev)
        rid = ev.message_id
        if rid:
            latest_by_id[rid] = record  # overwrite → last wins
        else:
            no_id.append(record)

    merged = list(latest_by_id.values()) + no_id
    merged.sort(key=lambda r: r[0])

    # Phase 2: build samples; resolve the window per-sample from the event's
    # own model (M4 — resume can switch model on the same session).
    samples: list[ContextSample] = []
    for _idx, gen, ev in merged:
        window = window_resolver(ev.model)
        samples.append(
            _cc_sample(
                ev,
                generation=gen,
                native_session_id=native_session_id,
                main_or_subagent=main_or_subagent,
                source_version=source_version,
                window=window,
                usage=ev.usage,
            )
        )
    return samples


def latest_main_ratio(samples: Iterable[ContextSample]) -> ContextSample | None:
    """The latest main-session sample = current占用 (082 alg 1).

    Mirrors CC's ``getCurrentUsage``: walk backwards, return the first main
    sample found. Subagent samples are skipped (C-3). Returns ``None`` if there
    is no main sample at all.
    """

    for sample in reversed(list(samples)):
        if sample.main_or_subagent == "main":
            return sample
    return None


# ---------------------------------------------------------------------------
# Codex — normalized events + calculator (slice-083 contract).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NormalizedCodexUsage:
    """One ``thread/tokenUsage/updated`` observation (slice-083).

    ``last_total_tokens`` is the current context占用 (numerator);
    ``total_total_tokens`` is cumulative cost and MUST NOT be used as the
    current占用. ``model_context_window`` is the trusted ratio denominator.
    """

    turn_id: str | None
    last_total_tokens: int | None
    total_total_tokens: int | None
    model_context_window: int | None
    timestamp: str | None = None


@dataclass(frozen=True)
class NormalizedCodexCompaction:
    """A ``contextCompaction`` item event.

    ``phase="completed"`` closes a compact boundary and starts a new
    generation (083 / A5). ``phase="started"`` does NOT —
    ``thread/compact/start`` returning ``{}`` only acknowledges the request.
    """

    phase: str
    turn_id: str | None
    timestamp: str | None = None


def _codex_sample(
    usage: NormalizedCodexUsage,
    *,
    generation: int,
    native_session_id: str,
    source_version: str | None,
) -> ContextSample:
    """Build one ContextSample from a Codex tokenUsage observation.

    - ``used = last.totalTokens`` (NEVER ``total.totalTokens``).
    - ``window = modelContextWindow``.
    - missing ``turnId`` → UNAVAILABLE (MISSING_REQUEST_IDENTITY), because a
      request without a stable identity cannot be deduped / replayed safely
      (M1).
    - missing ``last.totalTokens`` or ``modelContextWindow`` → UNAVAILABLE.
    """

    base = dict(
        native_session_id=native_session_id,
        main_or_subagent="main",  # Codex has no subagent transcript in this path
        turn_id=usage.turn_id,
        request_identity=usage.turn_id or "(no-turn)",
        generation=generation,
        source="codex",
        source_version=source_version,
    )
    if not usage.turn_id:
        return ContextSample(
            **base,
            input_tokens=None,
            cache_creation_input_tokens=None,
            cache_read_input_tokens=None,
            output_tokens=None,
            used_tokens=None,
            effective_window_tokens=usage.model_context_window,
            ratio=None,
            confidence=ContextConfidence.UNAVAILABLE,
            unavailable_reason=UnavailableReason.MISSING_REQUEST_IDENTITY,
        )
    last = usage.last_total_tokens
    window = usage.model_context_window
    if last is None:
        return ContextSample(
            **base,
            input_tokens=None,
            cache_creation_input_tokens=None,
            cache_read_input_tokens=None,
            output_tokens=None,
            used_tokens=None,
            effective_window_tokens=window,
            ratio=None,
            confidence=ContextConfidence.UNAVAILABLE,
            unavailable_reason=UnavailableReason.MISSING_USAGE,
        )
    if not window or window <= 0:
        return ContextSample(
            **base,
            input_tokens=None,
            cache_creation_input_tokens=None,
            cache_read_input_tokens=None,
            output_tokens=None,
            used_tokens=last,
            effective_window_tokens=None,
            ratio=None,
            confidence=ContextConfidence.UNAVAILABLE,
            unavailable_reason=UnavailableReason.UNKNOWN_WINDOW,
        )
    return ContextSample(
        **base,
        input_tokens=None,
        cache_creation_input_tokens=None,
        cache_read_input_tokens=None,
        output_tokens=None,
        used_tokens=last,
        effective_window_tokens=window,
        ratio=round(last / window, 4),
        confidence=ContextConfidence.RELIABLE,
        unavailable_reason=UnavailableReason.NONE,
    )


def extract_codex_samples(
    events: Sequence[NormalizedCodexUsage | NormalizedCodexCompaction],
    *,
    native_session_id: str,
    source_version: str | None = None,
) -> list[ContextSample]:
    """Walk a Codex context event stream and emit one ContextSample per
    ``tokenUsage/updated`` observation.

    Generation increments only on a ``contextCompaction`` item ``completed``
    event (A5); a ``started`` event (or ``thread/compact/start`` returning
    ``{}``) does NOT start a new generation. ``total.totalTokens`` is ignored
    as the current占用 — it is cumulative cost (083).
    """

    samples: list[ContextSample] = []
    generation = 0
    for ev in events:
        if isinstance(ev, NormalizedCodexCompaction):
            if ev.phase == "completed":
                generation += 1
            # "started" does not advance generation (A5).
            continue
        samples.append(
            _codex_sample(
                ev,
                generation=generation,
                native_session_id=native_session_id,
                source_version=source_version,
            )
        )
    return samples


# ---------------------------------------------------------------------------
# Journal codec — (de)serialize a ContextSample for the EventEnvelope payload.
# Kept here (next to the type) so the store and the reducer share one codec.
#


def context_sample_to_dict(sample: ContextSample) -> dict:
    """Serialize a ContextSample to a JSON-safe dict for the journal payload.

    Enums are reduced to their ``.value`` so ``json.dumps`` round-trips; the
    reverse is :func:`context_sample_from_dict`.
    """

    return {
        "main_or_subagent": sample.main_or_subagent,
        "turn_id": sample.turn_id,
        "request_identity": sample.request_identity,
        "generation": sample.generation,
        "input_tokens": sample.input_tokens,
        "cache_creation_input_tokens": sample.cache_creation_input_tokens,
        "cache_read_input_tokens": sample.cache_read_input_tokens,
        "output_tokens": sample.output_tokens,
        "used_tokens": sample.used_tokens,
        "effective_window_tokens": sample.effective_window_tokens,
        "ratio": sample.ratio,
        "source": sample.source,
        "source_version": sample.source_version,
        "confidence": sample.confidence.value,
        "unavailable_reason": sample.unavailable_reason.value,
    }


def context_sample_from_dict(
    d: Mapping[str, object], native_session_id: str
) -> ContextSample:
    """Reconstruct a ContextSample from its stored payload dict.

    ``native_session_id`` is injected from the envelope — the single ownership
    authority (codex review HIGH 5); the payload does NOT duplicate it.
    """

    return ContextSample(
        native_session_id=native_session_id,
        main_or_subagent=d["main_or_subagent"],  # type: ignore[assignment]
        turn_id=_opt_str(d.get("turn_id")),
        request_identity=str(d["request_identity"]),
        generation=int(d["generation"]),  # type: ignore[arg-type]
        input_tokens=_opt_int(d.get("input_tokens")),
        cache_creation_input_tokens=_opt_int(d.get("cache_creation_input_tokens")),
        cache_read_input_tokens=_opt_int(d.get("cache_read_input_tokens")),
        output_tokens=_opt_int(d.get("output_tokens")),
        used_tokens=_opt_int(d.get("used_tokens")),
        effective_window_tokens=_opt_int(d.get("effective_window_tokens")),
        ratio=_opt_float(d.get("ratio")),
        source=str(d["source"]),
        source_version=_opt_str(d.get("source_version")),
        confidence=ContextConfidence(str(d["confidence"])),
        unavailable_reason=UnavailableReason(str(d["unavailable_reason"])),
    )


def _opt_str(v: object) -> str | None:
    return None if v is None else str(v)


def _opt_int(v: object) -> int | None:
    if v is None:
        return None
    if isinstance(v, bool):
        return 0  # a bool is not a token count; align with _as_int
    if isinstance(v, (int, float)):
        return int(v)
    try:
        return int(str(v))
    except (TypeError, ValueError):
        return None


def _opt_float(v: object) -> float | None:
    if v is None:
        return None
    if isinstance(v, bool):
        return float(v)
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v))
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Runtime wiring (slice-088) — adapt a host-neutral AgentEvent stream (the SSE
# shape produced by /api/agent/sessions/{id}/messages) into the normalized
# events the calculators consume. Pure: no I/O. The hub subscription that
# actually feeds the live stream into the store lands in slice-090 (it needs
# the Episode↔native_session binding); 088 uses these adapters so the mapping
# is testable now and so a smoke run can drive the calculator from a real SSE.
#


def codex_context_events_from_agent(
    events: Sequence[Mapping[str, object]],
) -> list[NormalizedCodexUsage | NormalizedCodexCompaction]:
    """Extract normalized Codex context events from an AgentEvent stream.

    ``usage_updated`` → :class:`NormalizedCodexUsage` (``last.totalTokens`` /
    ``total.totalTokens`` / ``model_context_window`` straight off the
    translator's passthrough payload). ``compaction`` →
    :class:`NormalizedCodexCompaction` (phase, from the adapter).
    """

    out: list[NormalizedCodexUsage | NormalizedCodexCompaction] = []
    for ev in events:
        type_ = ev.get("type")
        payload = ev.get("payload")
        if not isinstance(payload, Mapping):
            payload = {}
        if type_ == "usage_updated":
            last = payload.get("last")
            total = payload.get("total")
            last_d = last if isinstance(last, Mapping) else {}
            total_d = total if isinstance(total, Mapping) else {}
            out.append(
                NormalizedCodexUsage(
                    turn_id=_opt_str(ev.get("turn_id")),
                    last_total_tokens=_opt_int(last_d.get("totalTokens")),
                    total_total_tokens=_opt_int(total_d.get("totalTokens")),
                    model_context_window=_opt_int(payload.get("model_context_window")),
                )
            )
        elif type_ == "compaction":
            phase = payload.get("phase", "unknown")  # only "completed" advances gen
            out.append(
                NormalizedCodexCompaction(
                    phase=str(phase),
                    turn_id=_opt_str(ev.get("turn_id")),
                )
            )
    return out


def cc_context_events_from_agent(
    events: Sequence[Mapping[str, object]],
) -> list[NormalizedCcEvent]:
    """Extract normalized CC context events from an AgentEvent stream.

    CC AgentEvents wrap TrowelEvent dicts (cc_adapter). The metering fields
    live on a dedicated ``context_usage`` event (slice-088): cc_host emits it
    from the assistant envelope BEFORE splitting into text/thinking/tool_use,
    because the split discards ``message.usage`` / ``message.model`` /
    ``message.id`` (codex code review HIGH 6). ``compact_boundary`` carries
    ``trigger`` ("auto"/"manual") from ``compactMetadata.trigger``.

    Field paths are pinned by the AgentEvent contract; a future TrowelEvent
    shape change would surface here as a test failure, not a silent miscount.
    """

    out: list[NormalizedCcEvent] = []
    for ev in events:
        type_ = ev.get("type")
        payload = ev.get("payload")
        if not isinstance(payload, Mapping):
            payload = {}
        if type_ == "compact_boundary":
            out.append(
                NormalizedCcEvent(
                    type="system",
                    subtype="compact_boundary",
                    timestamp=_opt_str(payload.get("timestamp")),
                    message_id=None,
                    model=None,
                    usage=None,
                    turn_id=_opt_str(ev.get("turn_id")),
                    compact_trigger=_opt_str(payload.get("trigger")),
                )
            )
        elif type_ == "context_usage":
            usage = payload.get("usage")
            out.append(
                NormalizedCcEvent(
                    type="assistant",
                    subtype=None,
                    timestamp=_opt_str(payload.get("timestamp")),
                    message_id=_opt_str(payload.get("message_id")),
                    model=_opt_str(payload.get("model")),
                    usage=usage if isinstance(usage, Mapping) else None,
                    turn_id=_opt_str(ev.get("turn_id")),
                )
            )
    return out
