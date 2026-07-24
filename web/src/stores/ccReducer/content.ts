import type {
  TextEvent,
  ThinkingEvent,
  ThinkingProgressEvent,
} from "../../api/ccTypes";
import type { ReducerState, Turn } from "./model";

/** 合并连续文本增量；非连续文本会成为新的时间线条目。 */
export function applyTextEvent(
  prev: ReducerState,
  event: TextEvent,
): ReducerState {
  const turns = prev.turns;
  if (turns.length === 0) return { ...prev, phase: "generating" };

  const last = turns[turns.length - 1];
  const lastItem = last.items[last.items.length - 1];
  const updated: Turn =
    lastItem?.kind === "text"
      ? {
          ...last,
          items: [
            ...last.items.slice(0, -1),
            { ...lastItem, text: lastItem.text + event.text },
          ],
        }
      : {
          ...last,
          items: [...last.items, { kind: "text", text: event.text }],
        };

  return {
    ...prev,
    phase: "generating",
    turns: [...turns.slice(0, -1), updated],
  };
}

/** 首次 heartbeat 固定思考起点，后续 heartbeat 只刷新 token 数。 */
export function applyThinkingProgress(
  prev: ReducerState,
  event: ThinkingProgressEvent,
): ReducerState {
  return {
    ...prev,
    phase: "thinking",
    meta: {
      ...prev.meta,
      thinkingStartedAt: prev.meta.thinkingStartedAt ?? Date.now(),
      thinkingTokens: event.estimated_tokens,
    },
  };
}

/** 合并思考增量，并在新条目上记录 live 或 history 提供的时长。 */
export function applyThinkingEvent(
  prev: ReducerState,
  event: ThinkingEvent,
): ReducerState {
  const turns = prev.turns;
  if (turns.length === 0) return { ...prev, phase: "thinking" };

  const last = turns[turns.length - 1];
  const lastItem = last.items[last.items.length - 1];
  if (lastItem?.kind === "thinking") {
    const updated: Turn = {
      ...last,
      items: [
        ...last.items.slice(0, -1),
        { ...lastItem, text: lastItem.text + event.text },
      ],
    };
    return {
      ...prev,
      phase: "thinking",
      turns: [...turns.slice(0, -1), updated],
    };
  }

  // live heartbeat 的起止时间优先于 history 回放携带的时长。
  const startedAt = prev.meta.thinkingStartedAt;
  const duration =
    startedAt !== null
      ? Math.max(1, Math.round((Date.now() - startedAt) / 1000))
      : event.thinking_duration_seconds;
  const updated: Turn = {
    ...last,
    items: [
      ...last.items,
      {
        kind: "thinking",
        text: event.text,
        thinkingDurationSeconds: duration,
      },
    ],
  };

  return {
    ...prev,
    phase: "thinking",
    meta: { ...prev.meta, thinkingStartedAt: null, thinkingTokens: null },
    turns: [...turns.slice(0, -1), updated],
  };
}
