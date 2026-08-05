/** 处理完成、失败和中断终态，并收口当前 turn 与会话阶段。 */

import type {
  ErrorEvent,
  FinishedEvent,
} from "../../transport/events";
import type {
  ReducerState,
  Turn,
  TurnItem,
  TurnStatus,
} from "./model";
import { claudeTurnTokens } from "./usage";

/** finished 统一结束当前 turn，并保留 history 已有的时长。 */
export function applyFinishedEvent(
  prev: ReducerState,
  event: FinishedEvent,
): ReducerState {
  const normalizedTokens = claudeTurnTokens(event.usage);
  const state: ReducerState = {
    ...prev,
    phase: "done",
    meta: {
      ...prev.meta,
      costUsd: event.total_cost_usd,
      numTurns: event.num_turns,
      lastTurnTokens:
        normalizedTokens ?? prev.meta.lastTurnTokens,
    },
  };
  const turns = prev.turns;
  if (turns.length === 0) return state;

  const last = turns[turns.length - 1];
  const rawDelta =
    last.startedAtMs !== undefined
      ? Math.round((Date.now() - last.startedAtMs) / 1000)
      : undefined;
  // 与 history 的时间戳差值规则一致：亚秒或时钟回拨不显示时长。
  const durationSeconds =
    rawDelta !== undefined && rawDelta > 0
      ? Math.max(1, rawDelta)
      : last.durationSeconds;
  const updatedLast: Turn = {
    ...last,
    status: "done",
    items: finalizeRunningTools(last.items),
    durationSeconds,
    startedAtMs: undefined,
  };

  return {
    ...state,
    turns: [...turns.slice(0, -1), updatedLast],
  };
}

export function applyErrorEvent(
  prev: ReducerState,
  event: ErrorEvent,
): ReducerState {
  return appendTerminalItem(
    { ...prev, phase: "error" },
    {
      kind: "error",
      subclass: event.subclass,
      errors: event.errors,
      apiErrorStatus: event.api_error_status,
    },
    "error",
  );
}

export function applyInterruptedEvent(prev: ReducerState): ReducerState {
  return appendTerminalItem(
    { ...prev, phase: "interrupted" },
    { kind: "interrupted" },
    "interrupted",
  );
}

function appendTerminalItem(
  prev: ReducerState,
  item: TurnItem,
  status: TurnStatus,
): ReducerState {
  const turns = prev.turns;
  if (turns.length === 0) return prev;

  const last = turns[turns.length - 1];
  const updatedLast: Turn = {
    ...last,
    items: [...finalizeRunningTools(last.items), item],
    status,
  };
  return { ...prev, turns: [...turns.slice(0, -1), updatedLast] };
}

/** 把根 turn 终态时仍在运行的工具标为结果事件缺失。 */
export function finalizeRunningTools(
  items: readonly TurnItem[],
): readonly TurnItem[] {
  return items.map((item) => {
    if (item.kind !== "tool") return item;
    return {
      ...item,
      status: item.status === "running" ? "missing" as const : item.status,
      childTools: finalizeRunningTools(item.childTools) as typeof item.childTools,
    };
  });
}
