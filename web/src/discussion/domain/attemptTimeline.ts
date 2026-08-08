/** 用 Agent 公共 reducer 构建彼此隔离的 participant attempt 时间线。 */

import {
  INITIAL_REDUCER_STATE,
  finalizeHistoryForView,
  reduceEvent,
  type ReducerState,
  type Turn,
  type TurnItem,
} from "../../agent/domain";
import {
  agentEventToTrowel,
  type AgentEvent,
  type AgentRuntime,
} from "../../agent/transport/agentEvent";

export type AttemptTimelineAvailability =
  | "live"
  | "loading"
  | "available"
  | "unavailable";

export interface DiscussionAttemptTimeline {
  readonly attemptId: string;
  readonly participantId: string;
  readonly roundNumber: number;
  readonly runtime: AgentRuntime;
  readonly reducer: ReducerState;
  readonly lastSeq: number | null;
  readonly terminalStatus: AttemptTerminalStatus | null;
  readonly needsReplay: boolean;
  readonly availability: AttemptTimelineAvailability;
}

export type AttemptTerminalStatus = "succeeded" | "failed" | "interrupted";

/** 为一个新 attempt 创建不伪造用户消息的空时间线。 */
export function createLiveAttemptTimeline(
  attemptId: string,
  participantId: string,
  roundNumber: number,
  runtime: AgentRuntime,
): DiscussionAttemptTimeline {
  return {
    attemptId,
    participantId,
    roundNumber,
    runtime,
    reducer: INITIAL_REDUCER_STATE,
    lastSeq: null,
    terminalStatus: null,
    needsReplay: false,
    availability: "live",
  };
}

/** 幂等接收一条实时事件，并在 seq 跳跃时留下补历史标记。 */
export function applyAttemptEvent(
  current: DiscussionAttemptTimeline,
  event: AgentEvent,
  attemptSequence: number,
): DiscussionAttemptTimeline {
  if (current.lastSeq !== null && attemptSequence <= current.lastSeq) return current;
  const missing =
    current.lastSeq !== null && attemptSequence > current.lastSeq + 1;
  const reducer = ensureActiveTurn(current.reducer, current.attemptId, event);
  const nextReducer = reduceEvent(reducer, agentEventToTrowel(event));
  return {
    ...current,
    runtime: event.runtime,
    reducer: nextReducer,
    lastSeq: attemptSequence,
    terminalStatus:
      terminalStatusFromReducer(nextReducer) ?? current.terminalStatus,
    needsReplay: current.needsReplay || missing,
    availability: "live",
  };
}

/** 用原生记录完整替换当前投影，终态回放会收口未完成项。 */
export function replayAttemptTimeline(
  current: DiscussionAttemptTimeline,
  events: readonly AgentEvent[],
  durableStatus: string,
): DiscussionAttemptTimeline {
  let reducer = INITIAL_REDUCER_STATE;
  for (const event of events) {
    reducer = ensureActiveTurn(reducer, current.attemptId, event);
    reducer = reduceEvent(reducer, agentEventToTrowel(event));
  }
  const terminalStatus = normalizeAttemptTerminalStatus(durableStatus);
  if (terminalStatus !== null) reducer = finalizeHistoryForView(reducer);
  return {
    ...current,
    reducer,
    lastSeq: current.lastSeq,
    terminalStatus,
    needsReplay: false,
    availability: events.length > 0 ? "available" : "unavailable",
  };
}

/** 取出 attempt 唯一逻辑轮的展示项。 */
export function attemptTimelineItems(
  timeline: DiscussionAttemptTimeline | undefined,
): readonly TurnItem[] {
  return timeline?.reducer.turns.at(-1)?.items ?? [];
}

/** 从 Agent reducer 的根 turn 派生观察者可见终态，不改共同公开状态机。 */
export function attemptTerminalStatus(
  timeline: DiscussionAttemptTimeline | undefined,
): AttemptTerminalStatus | null {
  return (
    timeline?.terminalStatus ??
    (timeline ? terminalStatusFromReducer(timeline.reducer) : null)
  );
}

/** 把共享 reducer 的根 turn 状态收敛成 discussion 使用的三类展示终态。 */
function terminalStatusFromReducer(
  reducer: ReducerState,
): AttemptTerminalStatus | null {
  const status = reducer.turns.at(-1)?.status;
  if (status === "done") return "succeeded";
  if (status === "error") return "failed";
  if (status === "interrupted") return "interrupted";
  return null;
}

/** 把持久 attempt 结果映射为 UI 展示终态；运行态保持空值。 */
function normalizeAttemptTerminalStatus(
  status: string,
): AttemptTerminalStatus | null {
  if (status === "succeeded") return "succeeded";
  if (status === "interrupted") return "interrupted";
  if (
    [
      "failed",
      "limited",
      "timed_out",
      "cancelled",
      "host_lost",
    ].includes(status)
  ) {
    return "failed";
  }
  return null;
}

/** 返回 Agent reducer 已计算的 attempt 时长，未形成时长时为空。 */
export function attemptDurationSeconds(
  timeline: DiscussionAttemptTimeline | undefined,
): number | null {
  return timeline?.reducer.turns.at(-1)?.durationSeconds ?? null;
}

/** 把最后一次工作之前的轨迹与后续最终回答分开。 */
export function splitCompletedItems(items: readonly TurnItem[]): {
  readonly workItems: readonly TurnItem[];
  readonly trailingText: string;
} {
  let lastWork = -1;
  for (let index = 0; index < items.length; index += 1) {
    if (items[index].kind !== "text") lastWork = index;
  }
  const trailing = items.slice(lastWork + 1);
  return {
    workItems: items.slice(0, lastWork + 1),
    trailingText: trailing
      .filter((item): item is Extract<TurnItem, { kind: "text" }> => item.kind === "text")
      .map((item) => item.text)
      .join("\n\n"),
  };
}

/** 在原生流没有重发公共 prompt 时补一个空的活跃 turn 容器。 */
function ensureActiveTurn(
  reducer: ReducerState,
  attemptId: string,
  event: AgentEvent,
): ReducerState {
  if (reducer.turns.length > 0 || event.type === "user") return reducer;
  const turn: Turn = {
    id: `discussion-attempt-${attemptId}`,
    userText: "",
    items: [],
    status: "active",
    turnId: event.turn_id,
    revertible: false,
  };
  return { ...reducer, turns: [turn], phase: "awaiting_first" };
}
