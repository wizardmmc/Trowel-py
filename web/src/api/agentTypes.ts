import type { TrowelEvent } from "./ccTypes";

export type AgentRuntime = "claude_code" | "codex";

/** 两种 runtime 共用的 AgentEvent v1 线协议。 */
export interface AgentEvent {
  readonly schema: "agent-event-v1";
  readonly session_id: string;
  readonly runtime: AgentRuntime;
  readonly seq: number;
  readonly type: string;
  readonly turn_id: string | null;
  readonly item_id: string | null;
  readonly payload: Readonly<Record<string, unknown>>;
}

export function agentEventToTrowel(event: AgentEvent): TrowelEvent {
  return {
    ...event.payload,
    type: event.type,
    turn_id: event.turn_id ?? undefined,
  } as unknown as TrowelEvent;
}
