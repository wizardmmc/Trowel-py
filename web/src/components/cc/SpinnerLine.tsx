import { useEffect, useState } from "react";

import { useCcStore } from "../../stores/ccStore";

/**
 * The "✻ thinking…" line shown at the tail of the message stream while CC is
 * thinking (slice-025-a A1).
 *
 * Driven by thinking_tokens heartbeats: the reducer flips phase to "thinking"
 * and records meta.thinkingStartedAt on the first heartbeat. The verb is picked
 * once per think and stays stable. Seconds + token count are hidden until 5s
 * elapse (tcc-defined threshold — NOT cc's 30s; see slice-025-a decision #3).
 * The effort suffix shows only when the user explicitly set an effort.
 *
 * Seconds use a local setInterval (cc itself uses local wall-clock, per
 * reverse_cc spec/05). Renders null outside the thinking phase.
 */
const SPINNER_VERBS = [
  "Pondering", "Synthesizing", "Analyzing", "Thinking", "Working",
  "Deliberating", "Composing", "Reasoning", "Reflecting", "Considering",
  "Processing", "Formulating", "Evaluating", "Exploring", "Investigating",
  "Planning", "Designing", "Drafting", "Refining", "Resolving",
  "Calculating", "Examining", "Reviewing", "Organizing", "Structuring",
  "Iterating", "Revising", "Mapping", "Tracing", "Unpacking",
  "Distilling", "Weighing",
] as const;

const FALLBACK_VERB = "Working";
/** tcc threshold for showing seconds/tokens (cc's source says 30s but实测 shows
 * earlier — slice-025-a decision #3 picks 5s). */
const SHOW_STATS_AFTER_MS = 5000;
const TICK_MS = 200;

function pickVerb(): string {
  return SPINNER_VERBS[Math.floor(Math.random() * SPINNER_VERBS.length)] ?? FALLBACK_VERB;
}

export function SpinnerLine() {
  const phase = useCcStore((s) => s.phase);
  const thinkingStartedAt = useCcStore((s) => s.meta.thinkingStartedAt);
  const thinkingTokens = useCcStore((s) => s.meta.thinkingTokens);
  const effort = useCcStore((s) => s.effort);

  // Pick one verb per think (stable within the think); cleared when not thinking.
  const [verb, setVerb] = useState<string | null>(null);
  useEffect(() => {
    if (phase === "thinking" && thinkingStartedAt !== null && verb === null) {
      setVerb(pickVerb());
    } else if (phase !== "thinking" && verb !== null) {
      setVerb(null);
    }
  }, [phase, thinkingStartedAt, verb]);

  // Local wall-clock tick so the seconds counter advances.
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (phase !== "thinking") return;
    const id = setInterval(() => setNow(Date.now()), TICK_MS);
    return () => clearInterval(id);
  }, [phase]);

  // Hide once the thinking envelope arrives (the thinking reducer case clears
  // thinkingStartedAt) even though phase is still "thinking" until the next
  // event flips it — avoids a brief "0s" re-render in the overlap window.
  if (phase !== "thinking" || thinkingStartedAt === null) return null;

  const elapsedMs =
    thinkingStartedAt !== null ? Math.max(0, now - thinkingStartedAt) : 0;
  const showStats = elapsedMs >= SHOW_STATS_AFTER_MS;
  const seconds = Math.floor(elapsedMs / 1000);
  const effortSuffix = effort ? `thinking with ${effort} effort` : "thinking";
  const displayVerb = verb ?? FALLBACK_VERB;

  return (
    <div
      className="cc-spinner"
      role="status"
      aria-live="polite"
      data-testid="cc-spinner"
    >
      <span className="cc-spinner__glyph" aria-hidden="true">✻</span>
      <span className="cc-spinner__verb">{displayVerb}…</span>
      {showStats && (
        <span className="cc-spinner__stats">
          <span className="cc-spinner__time">{seconds}s</span>
          {thinkingTokens !== null && thinkingTokens > 0 && (
            <span className="cc-spinner__tokens"> · ↓ {thinkingTokens} tokens</span>
          )}
          <span className="cc-spinner__think"> · {effortSuffix}</span>
        </span>
      )}
    </div>
  );
}
