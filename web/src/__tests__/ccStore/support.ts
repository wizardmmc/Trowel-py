import { beforeEach } from "vitest";
import {
  reduceEvent,
  INITIAL_REDUCER_STATE,
  _resetTurnIdCounterForTests,
  endActiveTurnOnStreamClose,
  finalizeHistoryForView,
  type ReducerState,
} from "../../stores/ccStore";
import type { TrowelEvent } from "../../api/ccTypes";

export {
  reduceEvent,
  INITIAL_REDUCER_STATE,
  _resetTurnIdCounterForTests,
  endActiveTurnOnStreamClose,
  finalizeHistoryForView,
};
export type { ReducerState, TrowelEvent };

export function installReducerTestReset(): void {
  beforeEach(() => {
    _resetTurnIdCounterForTests();
  });
}

export function run(events: TrowelEvent[]) {
  let state = { ...INITIAL_REDUCER_STATE };
  for (const ev of events) {
    state = reduceEvent(state, ev);
  }
  return state;
}

export function withOpenTurn(userText = "hi") {
  return reduceEvent(INITIAL_REDUCER_STATE, { type: "user", text: userText });
}
