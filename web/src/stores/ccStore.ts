/**
 * ccStore — CC session state: a pure event reducer + a zustand shell that
 * drives the live stream / REST calls.
 *
 * The reducer (`reduceEvent`) is the only place an event changes state. It is
 * pure and immutable (every update returns a new ReducerState via spread); the
 * zustand shell just feeds it events and tracks transport-level concerns
 * (the in-flight AbortController, the workdir, remembered effort).
 *
 * Both live SSE events and history-replay events go through the same reducer —
 * the `user` event (history-only) is what lets a replayed turn surface user
 * text without a second render path.
 */
import { create } from "zustand";

import {
  createSession as apiCreateSession,
  getHistory,
  interruptSession,
  listSessions,
  messagesUrl,
  type CcSession,
  type CcSessionSummary,
  type CreateSessionParams,
} from "../api/cc";
import { postMessageStream } from "../api/ccStream";
import type {
  ErrorEvent,
  RetryingEvent,
  TrowelEvent,
} from "../api/ccTypes";

// ---------------------------------------------------------------------------
// Data model
// ---------------------------------------------------------------------------

export type Phase =
  | "idle"
  | "awaiting_first"
  | "thinking"
  | "generating"
  | "tool"
  | "retrying"
  | "compacting"
  | "stalled"
  | "done"
  | "error"
  | "interrupted";

export type TurnStatus = "active" | "done" | "error" | "interrupted";

export interface ThinkingItem {
  readonly kind: "thinking";
  readonly text: string;
}

export interface TextItem {
  readonly kind: "text";
  readonly text: string;
}

export interface ToolItem {
  readonly kind: "tool";
  readonly toolUseId: string;
  readonly toolName: string;
  readonly input: Record<string, unknown>;
  readonly status: "running" | "done";
  readonly elapsedSeconds: number | null;
  readonly result: string | null;
}

export interface RetryingItem {
  readonly kind: "retrying";
  readonly attempt: number;
  readonly maxRetries: number | null;
  readonly errorStatus: number | null;
  readonly error: string | null;
  readonly retryDelayMs: number | null;
}

export interface StalledItem {
  readonly kind: "stalled";
}

export interface CompactBoundaryItem {
  readonly kind: "compact_boundary";
}

export interface LocalCommandItem {
  readonly kind: "local_command";
  readonly content: string;
}

export interface ErrorItem {
  readonly kind: "error";
  readonly subclass: string;
  readonly errors: readonly string[];
  readonly apiErrorStatus: number | null;
}

export interface InterruptedItem {
  readonly kind: "interrupted";
}

export type TurnItem =
  | ThinkingItem
  | TextItem
  | ToolItem
  | RetryingItem
  | StalledItem
  | CompactBoundaryItem
  | LocalCommandItem
  | ErrorItem
  | InterruptedItem;

export interface Turn {
  readonly id: string;
  readonly userText: string;
  readonly items: readonly TurnItem[];
  readonly status: TurnStatus;
}

export interface SessionMeta {
  readonly model: string | null;
  readonly ccSessionId: string | null;
  readonly costUsd: number | null;
  readonly numTurns: number | null;
  readonly hookFired: string | null;
}

export interface ReducerState {
  readonly turns: readonly Turn[];
  readonly phase: Phase;
  readonly meta: SessionMeta;
}

export const INITIAL_REDUCER_STATE: ReducerState = {
  turns: [],
  phase: "idle",
  meta: {
    model: null,
    ccSessionId: null,
    costUsd: null,
    numTurns: null,
    hookFired: null,
  },
};

// ---------------------------------------------------------------------------
// Reducer — pure, immutable
// ---------------------------------------------------------------------------

/** Generate a turn id. Injected so tests are deterministic. */
let _turnCounter = 0;
function nextTurnId(): string {
  _turnCounter += 1;
  return `turn-${_turnCounter}`;
}

/** Reset the turn id counter (tests only). */
export function _resetTurnIdCounterForTests(): void {
  _turnCounter = 0;
}

/** Append an item to the current (last) turn, immutably. */
function appendToCurrentTurn(
  prev: ReducerState,
  item: TurnItem,
  status?: TurnStatus,
): ReducerState {
  const turns = prev.turns;
  if (turns.length === 0) {
    return prev;
  }
  const last = turns[turns.length - 1];
  const updatedLast: Turn = {
    ...last,
    items: [...last.items, item],
    status: status ?? last.status,
  };
  return { ...prev, turns: [...turns.slice(0, -1), updatedLast] };
}

/** Update the last item if it matches a predicate, immutably. */
function updateLastItem(
  prev: ReducerState,
  predicate: (item: TurnItem) => boolean,
  update: (item: TurnItem) => TurnItem,
): ReducerState {
  const turns = prev.turns;
  if (turns.length === 0) return prev;
  const last = turns[turns.length - 1];
  const items = [...last.items];
  for (let i = items.length - 1; i >= 0; i--) {
    if (predicate(items[i])) {
      items[i] = update(items[i]);
      const updatedLast: Turn = { ...last, items };
      return { ...prev, turns: [...turns.slice(0, -1), updatedLast] };
    }
  }
  return prev;
}

/** Reduce one trowel event into a new ReducerState. Pure. */
export function reduceEvent(prev: ReducerState, event: TrowelEvent): ReducerState {
  switch (event.type) {
    case "session_started":
      return {
        ...prev,
        phase: prev.phase === "awaiting_first" ? "generating" : prev.phase,
        meta: {
          ...prev.meta,
          model: event.model,
          ccSessionId: event.cc_session_id,
        },
      };

    case "user": {
      // history-only: start a fresh turn carrying the user's text
      const turn: Turn = {
        id: nextTurnId(),
        userText: event.text,
        items: [],
        status: "active",
      };
      return { ...prev, turns: [...prev.turns, turn] };
    }

    case "text": {
      // append to the last text item if consecutive, else start a new one
      const turns = prev.turns;
      if (turns.length === 0) return { ...prev, phase: "generating" };
      const last = turns[turns.length - 1];
      const lastItem = last.items[last.items.length - 1];
      if (lastItem && lastItem.kind === "text") {
        const updated: Turn = {
          ...last,
          items: [
            ...last.items.slice(0, -1),
            { ...lastItem, text: lastItem.text + event.text },
          ],
        };
        return {
          ...prev,
          phase: "generating",
          turns: [...turns.slice(0, -1), updated],
        };
      }
      return appendToCurrentTurn(
        { ...prev, phase: "generating" },
        { kind: "text", text: event.text },
      );
    }

    case "thinking": {
      const turns = prev.turns;
      if (turns.length === 0) return { ...prev, phase: "thinking" };
      const last = turns[turns.length - 1];
      const lastItem = last.items[last.items.length - 1];
      if (lastItem && lastItem.kind === "thinking") {
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
      return appendToCurrentTurn(
        { ...prev, phase: "thinking" },
        { kind: "thinking", text: event.text },
      );
    }

    case "tool_call":
      return appendToCurrentTurn(
        { ...prev, phase: "tool" },
        {
          kind: "tool",
          toolUseId: event.tool_use_id,
          toolName: event.tool_name,
          input: event.input,
          status: "running",
          elapsedSeconds: null,
          result: null,
        },
      );

    case "tool_progress":
      return updateLastItem(
        { ...prev, phase: "tool" },
        (it) => it.kind === "tool" && it.toolUseId === event.tool_use_id,
        (it) =>
          it.kind === "tool"
            ? { ...it, elapsedSeconds: event.elapsed_time_seconds }
            : it,
      );

    case "tool_result":
      return updateLastItem(
        { ...prev, phase: "tool" },
        (it) => it.kind === "tool" && it.toolUseId === event.tool_use_id,
        (it) =>
          it.kind === "tool"
            ? { ...it, status: "done", result: event.content }
            : it,
      );

    case "retrying":
      return appendToCurrentTurn(
        { ...prev, phase: "retrying" },
        retryingItemFrom(event),
      );

    case "hook":
      return { ...prev, meta: { ...prev.meta, hookFired: event.hook_name } };

    case "status":
      if (event.stage === "compacting") {
        return { ...prev, phase: "compacting" };
      }
      return prev;

    case "compact_boundary":
      return appendToCurrentTurn(prev, { kind: "compact_boundary" });

    case "local_command":
      return appendToCurrentTurn(prev, {
        kind: "local_command",
        content: event.content,
      });

    case "finished": {
      // mark the current turn done (status mirrors phase so DOM/data
      // attributes don't lie about a finished turn still being "active")
      const turns = prev.turns;
      const state: ReducerState = {
        ...prev,
        phase: "done",
        meta: {
          ...prev.meta,
          costUsd: event.total_cost_usd,
          numTurns: event.num_turns,
        },
      };
      if (turns.length > 0) {
        const last = turns[turns.length - 1];
        const updatedLast: Turn = { ...last, status: "done" };
        return {
          ...state,
          turns: [...turns.slice(0, -1), updatedLast],
        };
      }
      return state;
    }

    case "error":
      return appendToCurrentTurn(
        { ...prev, phase: "error" },
        {
          kind: "error",
          subclass: event.subclass,
          errors: event.errors,
          apiErrorStatus: event.api_error_status,
        },
        "error",
      );

    case "interrupted":
      return appendToCurrentTurn(
        { ...prev, phase: "interrupted" },
        { kind: "interrupted" },
        "interrupted",
      );

    case "stalled":
      return appendToCurrentTurn({ ...prev, phase: "stalled" }, { kind: "stalled" });

    default:
      return prev;
  }
}

function retryingItemFrom(event: RetryingEvent): RetryingItem {
  return {
    kind: "retrying",
    attempt: event.attempt,
    maxRetries: event.max_retries,
    errorStatus: event.error_status,
    error: event.error,
    retryDelayMs: event.retry_delay_ms,
  };
}

// ---------------------------------------------------------------------------
// zustand shell
// ---------------------------------------------------------------------------

interface CcState extends ReducerState {
  /** Remembered from createSession params (session_started has no effort field). */
  readonly effort: string | null;
  /** Current trowel session id (null until createSession resolves). */
  readonly sessionId: string | null;
  readonly workdir: string | null;
  /** Transport-level error (fetch failure etc.), separate from CC error events. */
  readonly transportError: string | null;
  /** History list for the switcher. */
  readonly history: readonly CcSessionSummary[];
  readonly loadingHistory: boolean;

  _abort: AbortController | null;

  // actions
  startSession: (params: CreateSessionParams) => Promise<CcSession | null>;
  refreshHistory: (workdir: string) => Promise<void>;
  loadHistoryIntoView: () => Promise<void>;
  send: (text: string) => Promise<void>;
  interrupt: () => Promise<void>;
  reset: () => void;
}

export const useCcStore = create<CcState>((set, get) => {
  /** Apply one event through the reducer. */
  function apply(event: TrowelEvent): void {
    set((state) => reduceEvent(state, event));
  }

  return {
    ...INITIAL_REDUCER_STATE,
    effort: null,
    sessionId: null,
    workdir: null,
    transportError: null,
    history: [],
    loadingHistory: false,
    _abort: null,

    startSession: async (params) => {
      get()._abort?.abort();
      set({
        ...INITIAL_REDUCER_STATE,
        effort: params.effort ?? null,
        workdir: params.workdir,
        sessionId: null,
        transportError: null,
      });
      try {
        const session = await apiCreateSession(params);
        set({ sessionId: session.session_id });
        return session;
      } catch (err) {
        set({ transportError: (err as Error).message });
        return null;
      }
    },

    refreshHistory: async (workdir) => {
      set({ loadingHistory: true });
      try {
        const items = await listSessions(workdir);
        set({ history: items, loadingHistory: false });
      } catch (err) {
        set({
          loadingHistory: false,
          transportError: (err as Error).message,
        });
      }
    },

    loadHistoryIntoView: async () => {
      const sid = get().sessionId;
      if (!sid) return;
      try {
        const events = await getHistory(sid);
        set((state) => {
          let next: ReducerState = {
            ...INITIAL_REDUCER_STATE,
            meta: state.meta,
          };
          for (const ev of events) {
            next = reduceEvent(next, ev);
          }
          return next;
        });
      } catch (err) {
        set({ transportError: (err as Error).message });
      }
    },

    send: async (text) => {
      const sid = get().sessionId;
      if (!sid) {
        set({ transportError: "no active session" });
        return;
      }
      // Refuse a second concurrent send: two streams into one reducer would
      // interleave deltas and corrupt the text/thinking accumulators. The
      // composer also swaps to an interrupt button while streaming, but Enter
      // can still race — guard at the store.
      if (get()._abort) {
        return;
      }
      // optimistic: start a user turn immediately (live stream has no user event)
      const turn: Turn = {
        id: nextTurnId(),
        userText: text,
        items: [],
        status: "active",
      };
      const abort = new AbortController();
      set((state) => ({
        turns: [...state.turns, turn],
        phase: "awaiting_first",
        transportError: null,
        _abort: abort,
      }));

      try {
        await postMessageStream(
          messagesUrl(sid),
          { text },
          apply,
          { signal: abort.signal },
        );
      } catch (err) {
        set({ transportError: (err as Error).message });
      } finally {
        set({ _abort: null });
      }
    },

    interrupt: async () => {
      const sid = get().sessionId;
      get()._abort?.abort();
      if (!sid) return;
      try {
        await interruptSession(sid);
      } catch (err) {
        set({ transportError: (err as Error).message });
      }
    },

    reset: () => {
      get()._abort?.abort();
      set({
        ...INITIAL_REDUCER_STATE,
        effort: null,
        sessionId: null,
        workdir: null,
        transportError: null,
        history: [],
        loadingHistory: false,
        _abort: null,
      });
    },
  };
});

// re-export for component convenience
export type { CcSession, CcSessionSummary, CreateSessionParams };
export type { ErrorEvent };
