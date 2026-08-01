/** 定义统一 AgentEvent 信封，并转换为前端 reducer 使用的事件。 */

import type { TrowelEvent } from "./events";

export type Runtime = "claude_code" | "codex";
export type AgentRuntime = Runtime;

/** 两种 runtime 共用的 AgentEvent v1 线协议。 */
export interface AgentEvent {
  readonly schema: "agent-event-v1";
  readonly session_id: string;
  readonly runtime: AgentRuntime;
  readonly seq: number;
  readonly type: string;
  readonly thread_id: string | null;
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
