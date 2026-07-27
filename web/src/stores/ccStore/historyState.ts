import type { AgentEventLike } from "../../api/agent";
import { agentEventToTrowel } from "../../api/agentTypes";
import {
  finalizeHistoryForView,
  INITIAL_REDUCER_STATE,
  reduceEvent,
  type ReducerState,
} from "../ccReducer";
import type { PerSessionState } from "./sessionState";
import { reduceCodexSubagentEvent } from "./codexSubagents";

/** 回放独立的 history seq，并重置 live watermark。 */
export function replayAgentHistory(
  session: PerSessionState,
  envelopes: readonly AgentEventLike[],
): PerSessionState {
  let next: PerSessionState = {
    ...session,
    ...INITIAL_REDUCER_STATE,
    meta: session.meta,
    codexSubagents: {},
  };
  let replaySeq: number | null = null;

  for (const envelope of envelopes) {
    if (replaySeq !== null && envelope.seq <= replaySeq) continue;
    replaySeq = envelope.seq;
    const childReduced = reduceCodexSubagentEvent(next, envelope);
    next =
      childReduced ?? {
        ...next,
        ...reduceEvent(next, agentEventToTrowel(envelope)),
      };
  }

  return {
    ...session,
    ...finalizeHistoryForView(next as ReducerState),
    codexSubagents: next.codexSubagents,
    // thread/goal/get 是当前事实；历史 turn 里的 Goal 通知可能已经过时。
    goal: session.goal,
    // Codex thread/read 不提供可信的当前 Plan，不能从旧 turn 回放恢复。
    plan: null,
    // history 和 live 的 seq 都从 1 开始，不能共享 watermark。
    lastSeq: null,
    needsReplay: false,
  };
}
