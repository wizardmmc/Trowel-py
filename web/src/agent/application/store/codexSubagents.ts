import type { SubagentProgressEvent } from "../../transport/events";
import type { AgentEvent } from "../../transport/agentEvent";
import type { AgentEventLike } from "../../transport/api";
import { agentEventToTrowel } from "../../transport/agentEvent";
import {
  INITIAL_REDUCER_STATE,
  reduceEvent,
  type ReducerState,
  type SubagentState,
  type Turn,
  type TurnItem,
} from "../../domain/reducer";
import type {
  CodexSubagentThread,
  PerSessionState,
} from "./sessionState";

type ChildStatus = CodexSubagentThread["status"];

function activityStatus(kind: unknown): ChildStatus {
  if (kind === "interrupted") return "cancelled";
  if (kind === "started") return "started";
  return "progress";
}

function terminalStatus(event: AgentEvent, current: ChildStatus): ChildStatus {
  if (event.type === "finished") return "completed";
  if (event.type === "interrupted") return "cancelled";
  if (event.type === "error") return "failed";
  if (current === "started" && event.type !== "turn_start") return "progress";
  return current;
}

function activityEvent(
  event: AgentEvent,
  childThreadId: string,
  parentThreadId: string,
): SubagentProgressEvent {
  return {
    type: "subagent_progress",
    tool_use_id: `codex:${childThreadId}`,
    task_id: childThreadId,
    status: activityStatus(event.payload.kind),
    subagent_type: null,
    agent_thread_id: childThreadId,
    parent_thread_id: parentThreadId,
    agent_path:
      typeof event.payload.agent_path === "string"
        ? event.payload.agent_path
        : null,
  };
}

function replaceStatus(
  subagent: SubagentState,
  threadId: string,
  status: ChildStatus,
): SubagentState {
  return subagent.agentThreadId === threadId
    ? { ...subagent, status }
    : subagent;
}

function updateItems(
  items: readonly TurnItem[],
  threadId: string,
  status: ChildStatus,
): readonly TurnItem[] {
  return items.map((item) => {
    if (item.kind === "subagent") {
      return { ...item, subagent: replaceStatus(item.subagent, threadId, status) };
    }
    if (item.kind === "tool") {
      return {
        ...item,
        subagent: item.subagent
          ? replaceStatus(item.subagent, threadId, status)
          : undefined,
        childTools: item.childTools.map((child) => ({
          ...child,
          subagent: child.subagent
            ? replaceStatus(child.subagent, threadId, status)
            : undefined,
        })),
      };
    }
    return item;
  });
}

function updateBlockStatus(
  state: ReducerState,
  threadId: string,
  status: ChildStatus,
): ReducerState {
  const containsTarget = state.turns.some((turn) =>
    turn.items.some((item) => itemContainsSubagent(item, threadId)),
  );
  if (!containsTarget) return state;
  return {
    ...state,
    turns: state.turns.map(
      (turn): Turn => ({
        ...turn,
        items: updateItems(turn.items, threadId, status),
      }),
    ),
  };
}

function itemContainsSubagent(item: TurnItem, threadId: string): boolean {
  if (item.kind === "subagent") {
    return item.subagent.agentThreadId === threadId;
  }
  if (item.kind !== "tool") return false;
  if (item.subagent?.agentThreadId === threadId) return true;
  return item.childTools.some((child) => itemContainsSubagent(child, threadId));
}

function withReducerState(
  session: PerSessionState,
  reduced: ReducerState,
): PerSessionState {
  return { ...session, ...reduced };
}

function wouldCreateCycle(
  registry: PerSessionState["codexSubagents"],
  childThreadId: string,
  parentThreadId: string,
): boolean {
  let current = parentThreadId;
  const visited = new Set<string>();
  while (!visited.has(current)) {
    if (current === childThreadId) return true;
    visited.add(current);
    const parent = registry[current];
    if (!parent) return false;
    current = parent.parentThreadId;
  }
  return true;
}

/** Return null when the event belongs to the root reducer. */
export function reduceCodexSubagentEvent(
  session: PerSessionState,
  event: AgentEvent,
): PerSessionState | null {
  const eventThreadId = event.thread_id;
  const rootThreadId = session.nativeSessionId;
  if (event.runtime !== "codex" || !eventThreadId || !rootThreadId) return null;

  if (
    event.type === "subagent_activity" &&
    event.payload.source === "subagent_activity" &&
    typeof event.payload.agent_thread_id === "string" &&
    event.payload.agent_thread_id
  ) {
    const childThreadId = event.payload.agent_thread_id;
    if (wouldCreateCycle(session.codexSubagents, childThreadId, eventThreadId)) {
      return session;
    }
    const progress = activityEvent(event, childThreadId, eventThreadId);
    const previous = session.codexSubagents[childThreadId];
    const child: CodexSubagentThread = {
      threadId: childThreadId,
      parentThreadId: eventThreadId,
      agentPath:
        typeof event.payload.agent_path === "string"
          ? event.payload.agent_path
          : previous?.agentPath ?? null,
      status: activityStatus(event.payload.kind),
      state: previous?.state ?? { ...INITIAL_REDUCER_STATE },
      historyLoaded: previous?.historyLoaded ?? false,
      historyLoading: previous?.historyLoading ?? false,
      historyError: previous?.historyError ?? null,
    };
    const registry = { ...session.codexSubagents, [childThreadId]: child };
    if (eventThreadId === rootThreadId) {
      return {
        ...withReducerState(session, reduceEvent(session, progress)),
        codexSubagents: registry,
      };
    }
    const parent = registry[eventThreadId];
    if (!parent) return { ...session, codexSubagents: registry };
    return {
      ...session,
      codexSubagents: {
        ...registry,
        [eventThreadId]: {
          ...parent,
          state: reduceEvent(parent.state, progress),
        },
      },
    };
  }

  if (eventThreadId === rootThreadId) return null;
  const child = session.codexSubagents[eventThreadId];
  if (!child) return session;

  const status = terminalStatus(event, child.status);
  const childState = reduceEvent(child.state, agentEventToTrowel(event));
  const rootReducer = updateBlockStatus(session, eventThreadId, status);
  let rootState: PerSessionState = { ...session, ...rootReducer };
  const registry: Record<string, CodexSubagentThread> = {
    ...session.codexSubagents,
    [eventThreadId]: { ...child, status, state: childState },
  };
  for (const [threadId, candidate] of Object.entries(registry)) {
    registry[threadId] = {
      ...candidate,
      state: updateBlockStatus(candidate.state, eventThreadId, status),
    };
  }
  rootState = { ...rootState, codexSubagents: registry };
  return rootState;
}

export function replayCodexSubagentHistory(
  session: PerSessionState,
  threadId: string,
  envelopes: readonly AgentEventLike[],
): PerSessionState {
  const current = session.codexSubagents[threadId];
  if (!current) return session;
  let next: PerSessionState = {
    ...session,
    codexSubagents: {
      ...session.codexSubagents,
      [threadId]: {
        ...current,
        state: { ...INITIAL_REDUCER_STATE },
        historyLoading: false,
        historyError: null,
      },
    },
  };
  for (const envelope of envelopes) {
    const reduced = reduceCodexSubagentEvent(next, envelope as AgentEvent);
    if (reduced !== null) next = reduced;
  }
  const replayed = next.codexSubagents[threadId];
  return {
    ...next,
    codexSubagents: {
      ...next.codexSubagents,
      [threadId]: {
        ...replayed,
        historyLoaded: true,
        historyLoading: false,
        historyError: null,
      },
    },
  };
}
