/**
 * AgentEvent v1 — the single wire shape both runtimes emit (slice-074).
 *
 * Mirrors `trowel_py/schemas/agent_host.py::AgentEvent` 1:1. After slice-074 the
 * frontend consumes this envelope on every path (live SSE + history replay);
 * CC and Codex no longer have separate wire contracts.
 *
 * Design (people-confirmed 2026-07-19): unified to the TrowelEvent type
 * vocabulary (the CC contract in `ccTypes.ts`), so Codex events arrive already
 * renamed (assistant_delta→text, reasoning_delta→thinking, tool_started→
 * tool_call, tool_completed→tool_result). Two Codex events with no CC
 * equivalent keep their own type as extensions: `usage_updated` (per-turn token
 * accounting) and `host_status` (manager ready/degraded/host_exited).
 *
 * `seq` is per-session monotonic (≥1); the store drops dups and flags gaps
 * (spec §3). `payload` carries the per-type fields the reducer reads — the same
 * field names `ccTypes.ts` always used, just nested under `payload`.
 */
import type { TrowelEvent } from "./ccTypes";

/** Runtime tag on every envelope (frozen at session create, spec C-1). */
export type AgentRuntime = "claude_code" | "codex";

/** The unified envelope both runtimes emit after their adapter wraps them. */
export interface AgentEvent {
  readonly schema: "agent-event-v1";
  readonly session_id: string;
  readonly runtime: AgentRuntime;
  /** Per-session monotonic sequence (≥1). Cross-session seqs are never compared. */
  readonly seq: number;
  /** Discriminator: a TrowelEvent type name, or a Codex extension (usage_updated / host_status). */
  readonly type: string;
  /** Native or trowel turn id when known; null otherwise. */
  readonly turn_id: string | null;
  /** Native item id when the event is about a specific item (stable across started/delta/completed). */
  readonly item_id: string | null;
  /** Per-type fields (read as-is by the reducer after unwrapping). */
  readonly payload: Readonly<Record<string, unknown>>;
}

/**
 * Unwrap an AgentEvent envelope into the flat TrowelEvent shape the reducer
 * consumes (slice-074).
 *
 * The reducer (`ccReducer.reduceEvent`) switches on `type` and reads flat
 * fields (`event.text`, `event.tool_use_id`, …). Rather than rewrite the
 * reducer + its 100+ tests to read `payload.*`, the envelope is unwrapped back
 * into the flat shape at the store boundary. `turn_id` from the envelope is
 * stamped on top (Codex's adapter carries turn_id on the envelope, not in the
 * payload; CC's payload already has it, so the override is a harmless no-op).
 *
 * The cast is unavoidable — `payload` is `Record<string, unknown>` on the wire
 * — but safe: the backend adapter built it from typed Pydantic models, and the
 * `type` discriminator guarantees the payload matches one TrowelEvent variant.
 */
export function agentEventToTrowel(event: AgentEvent): TrowelEvent {
  return {
    ...event.payload,
    type: event.type,
    turn_id: event.turn_id ?? undefined,
  } as unknown as TrowelEvent;
}
