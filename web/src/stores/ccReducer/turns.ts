import type { TurnStartEvent, UserEvent } from "../../api/ccTypes";
import type { ReducerState, Turn } from "./model";

let turnCounter = 0;

export function nextTurnId(): string {
  turnCounter += 1;
  return `turn-${turnCounter}`;
}

export function _resetTurnIdCounterForTests(): void {
  turnCounter = 0;
}

/** 把后端 turn 信息写回 store 预先创建的乐观 turn。 */
export function applyTurnStart(
  prev: ReducerState,
  event: TurnStartEvent,
): ReducerState {
  const turns = prev.turns;
  if (turns.length === 0) return prev;

  const last = turns[turns.length - 1];
  const updated: Turn = {
    ...last,
    // Codex 可能不提供 turn_id，此时保留乐观 turn 已有的值。
    turnId: event.turn_id ?? last.turnId,
    revertible: event.revertible,
  };
  return { ...prev, turns: [...turns.slice(0, -1), updated] };
}

/** 对齐 history user 事件与 Codex live user echo。 */
export function applyUserEvent(
  prev: ReducerState,
  event: UserEvent,
): ReducerState {
  const turns = prev.turns;
  if (turns.length > 0) {
    const last = turns[turns.length - 1];
    if (
      last.status === "active" &&
      last.items.length === 0 &&
      last.userText === event.text
    ) {
      return prev;
    }
  }

  const turn: Turn = {
    id: nextTurnId(),
    userText: event.text,
    items: [],
    status: "active",
    turnId: null,
    revertible: false,
    durationSeconds: event.duration_seconds,
  };
  return { ...prev, turns: [...turns, turn] };
}
