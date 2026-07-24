import type { AgentEventLike } from "../../api/agent";
import { agentEventToTrowel } from "../../api/agentTypes";
import {
  finalizeHistoryForView,
  INITIAL_REDUCER_STATE,
  reduceEvent,
  type ReducerState,
} from "../ccReducer";
import type { PerSessionState } from "./sessionState";

/** 回放独立的 history seq，并重置 live watermark。 */
export function replayAgentHistory(
  session: PerSessionState,
  envelopes: readonly AgentEventLike[],
): PerSessionState {
  let next: ReducerState = {
    ...INITIAL_REDUCER_STATE,
    meta: session.meta,
  };
  let replaySeq: number | null = null;

  for (const envelope of envelopes) {
    if (replaySeq !== null && envelope.seq <= replaySeq) continue;
    replaySeq = envelope.seq;
    next = reduceEvent(next, agentEventToTrowel(envelope));
  }

  return {
    ...session,
    ...finalizeHistoryForView(next),
    // history 和 live 的 seq 都从 1 开始，不能共享 watermark。
    lastSeq: null,
    needsReplay: false,
  };
}
