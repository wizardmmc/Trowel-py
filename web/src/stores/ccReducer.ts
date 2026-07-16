/**
 * ccReducer — CC session event reducer (pure, immutable).
 *
 * The ONLY place a TrowelEvent changes session state. Every case returns a new
 * ReducerState via spread (never mutates). Both live SSE events and history-
 * replay events flow through the same `reduceEvent` — the `user` event
 * (history-only) is what lets a replayed turn surface user text without a
 * second render path.
 *
 * Sibling file `ccStore.ts` wraps this reducer in a multi-session zustand shell
 * (sessions dict + activeSid + transport). This module has no zustand / API /
 * transport dependencies — it's pure data-in → state-out, which keeps the
 * reducer unit-testable in isolation (see ccStore.test.ts).
 */
import type {
  QuestionInput,
  RetryingEvent,
  SubagentProgressEvent,
  ToolCallEvent,
  TrowelEvent,
  WorkflowAgentInfo,
  WorkflowPhaseInfo,
  WorkflowTreeEvent,
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
  | "awaiting_input"
  | "done"
  | "error"
  | "interrupted";

export type TurnStatus = "active" | "done" | "error" | "interrupted";

export interface ThinkingItem {
  readonly kind: "thinking";
  readonly text: string;
  /** Seconds the think took (first heartbeat -> thinking content envelope).
   * Undefined when no heartbeat preceded (e.g. non-GLM backend or history replay). */
  readonly thinkingDurationSeconds?: number;
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
  /** slice-029: BE-computed diff for a Write tool_use (overwriting an existing
   * file). Absent for Edit/MultiEdit (FE computes those from input) and for
   * Write-create. Copied from ToolCallEvent.write_diff so live + replay render
   * identically. */
  readonly writeDiff?: import("../api/ccTypes").WriteDiff;
  /** Present when this is an Agent tool call with sub-agent progress attached
   * (slice-025-a A3). */
  readonly subagent?: SubagentState;
  /** Child tool_uses spawned inside a sub-agent (their envelope
   * parent_tool_use_id points at this tool's id). Recursive — a child may
   * carry its own children for nested sub-agents (slice-025-a 阶段B). */
  readonly childTools: readonly ToolItem[];
}

/** Merged sub-agent progress (fields refreshed by each task_* event, newest
 * wins; undefined fields fall back to the previous value so started's
 * description/subagent_type survive into the progress/completed updates). */
export interface SubagentState {
  readonly status: "started" | "progress" | "completed";
  readonly description?: string | null;
  readonly subagent_type?: string | null;
  readonly last_tool_name?: string | null;
  readonly usage?: Record<string, unknown> | null;
}

/** Standalone sub-agent row — the degradation path when a subagent_progress
 * event arrives with no matching Agent ToolItem (slice-025-a decision #10:
 * never lose the event). */
export interface SubagentItem {
  readonly kind: "subagent";
  readonly toolUseId: string;
  readonly subagent: SubagentState;
}

export interface RetryingItem {
  readonly kind: "retrying";
  readonly attempt: number;
  readonly maxRetries: number | null;
  readonly errorStatus: number | null;
  readonly error: string | null;
  readonly retryDelayMs: number | null;
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

/** AskUserQuestion inline selection box (slice-025-c). Pending while the user
 * is choosing; flips to "answered"/"declined" when the matching tool_result
 * arrives (same tool_use_id). resultText carries cc's "User has answered..."
 * text for the completed-state echo. */
export interface ElicitationItem {
  readonly kind: "elicit";
  readonly toolUseId: string;
  readonly requestId: string;
  readonly questions: ReadonlyArray<Readonly<QuestionInput>>;
  readonly status: "pending" | "answered" | "declined";
  readonly resultText: string | null;
  readonly answers: Readonly<Record<string, string>> | null;
}

/** One workflow run, rendered as a collapsible progress tree (slice-036).
 * Mirrors WorkflowTreeEvent with wire snake_case → internal camelCase. The
 * reducer matches by runId so a full snapshot replaces the prior one. Lives
 * as a turn item (the turn that launched it); a workflow that finishes on a
 * later turn still updates the item in its launch turn (scanned across turns). */
export interface WorkflowItem {
  readonly kind: "workflow";
  readonly runId: string;
  readonly taskId: string | null;
  readonly name: string;
  readonly args: string | null;
  readonly status: "running" | "completed" | "killed" | "failed";
  readonly agentCount: number;
  readonly doneCount: number;
  readonly totalTokens: number | null;
  readonly totalToolCalls: number | null;
  readonly durationMs: number | null;
  readonly phases: ReadonlyArray<Readonly<WorkflowPhaseInfo>>;
  readonly agents: ReadonlyArray<Readonly<WorkflowAgentInfo>>;
  readonly error: string | null;
}

export type TurnItem =
  | ThinkingItem
  | TextItem
  | ToolItem
  | SubagentItem
  | RetryingItem
  | CompactBoundaryItem
  | LocalCommandItem
  | ErrorItem
  | InterruptedItem
  | ElicitationItem
  | WorkflowItem;

export interface Turn {
  readonly id: string;
  readonly userText: string;
  readonly items: readonly TurnItem[];
  readonly status: TurnStatus;
  /** slice-026: backend checkpoint turn_id (the ref name). Set by the live
   * TurnStartEvent; null for history-replayed turns (no checkpoint) and until
   * the TurnStartEvent arrives. */
  readonly turnId: string | null;
  /** slice-026: whether the user may revert to this turn. True only when the
   * workdir is a git repo AND the turn saved a checkpoint (TurnStartEvent said
   * revertible=true). History turns are never revertible. */
  readonly revertible: boolean;
}

export interface SessionMeta {
  readonly model: string | null;
  readonly ccSessionId: string | null;
  readonly costUsd: number | null;
  readonly numTurns: number | null;
  readonly hookFired: string | null;
  /** Wall-clock ms of the first thinking_tokens heartbeat (slice-025-a A1).
   * Set on first heartbeat, cleared when the thinking content envelope arrives
   * (the duration is stamped onto the ThinkingItem). Null outside a think. */
  readonly thinkingStartedAt: number | null;
  /** Cumulative thinking-token estimate from the latest heartbeat. */
  readonly thinkingTokens: number | null;
  /** Stall phased heads-up (slice-029). Set when cc has been silent past
   * threshold_mild/severe; cleared by any subsequent event (cc is alive again).
   * Null when no heads-up is active. The process is NOT killed on mild/severe —
   * only the 30-min hard cap (ErrorEvent subclass="stalled") ends the turn. */
  readonly stallWarning: { severity: "mild" | "severe"; elapsed_s: number } | null;
  /** slice-028 bug3: the CC subprocess exited (user /exit or died after a turn).
   * Set by the session_exited event. The MultiSessionBar greys the row out and,
   * if it was the active session, the shell unsets activeSid so the view returns
   * to the no-active-session state. */
  readonly exited: boolean;
  /** slice-028 bug3: the CC subprocess exit code (null until session_exited). */
  readonly exitReturncode: number | null;
}

/** slice-028 V2 tasks (TaskCreate/TaskUpdate). Mirrors cc's V2 task model:
 * TaskCreate input = {subject, description?, activeForm?} (no taskId — it's
 * assigned by cc and returned in the tool_result text "Task #N created
 * successfully"). TaskUpdate input = {taskId, status}. Tasks are session-scoped
 * (cross-turn) so they live in ReducerState, not inside any one turn. */
export interface Task {
  /** The cc-assigned task id ("1", "2", …). Null from TaskCreate until the
   * matching tool_result parses it out of "Task #N created successfully". */
  readonly taskId: string | null;
  /** The TaskCreate tool_use_id — the stable key we have before the result
   * arrives, used to route the result back to the right task. */
  readonly toolUseId: string;
  readonly subject: string;
  readonly description?: string;
  readonly activeForm?: string;
  readonly status: "pending" | "in_progress" | "completed";
}

export interface ReducerState {
  readonly turns: readonly Turn[];
  readonly phase: Phase;
  readonly meta: SessionMeta;
  /** slice-028: V2 task list (session-scoped). */
  readonly tasks: readonly Task[];
}

export const INITIAL_REDUCER_STATE: ReducerState = {
  turns: [],
  phase: "idle",
  tasks: [],
  meta: {
    model: null,
    ccSessionId: null,
    costUsd: null,
    numTurns: null,
    hookFired: null,
    thinkingStartedAt: null,
    thinkingTokens: null,
    stallWarning: null,
    exited: false,
    exitReturncode: null,
  },
};

// ---------------------------------------------------------------------------
// Reducer — pure, immutable
// ---------------------------------------------------------------------------

/** Generate a turn id. Injected so tests are deterministic. Exported because
 * the zustand shell (ccStore) also stamps optimistic turn ids on send(). */
let _turnCounter = 0;
export function nextTurnId(): string {
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

/** Recursively transform the ToolItem whose toolUseId matches, anywhere in the
 * items tree (top-level or nested inside childTools). Returns a new items array
 * if found, else null. tool_use_id is globally unique so no parent field is
 * needed on progress/result events (slice-025-a 阶段B). */
function updateToolInTree(
  items: readonly TurnItem[],
  toolUseId: string,
  update: (tool: ToolItem) => ToolItem,
): readonly TurnItem[] | null {
  let found = false;
  // childTools is always ToolItem[] (a sub-agent only spawns tool calls), so
  // recurse with a ToolItem→ToolItem helper. Splitting this from the top-level
  // walk keeps the types straight: childTools is narrower than the items array
  // (which carries every TurnItem kind), and routing it through the same
  // TurnItem[] walk would widen it and fail to assign back onto a ToolItem.
  const updateTool = (it: ToolItem): ToolItem => {
    if (it.toolUseId === toolUseId) {
      found = true;
      return update(it);
    }
    if (it.childTools.length > 0) {
      return { ...it, childTools: it.childTools.map(updateTool) };
    }
    return it;
  };
  const result = items.map((it) => (it.kind === "tool" ? updateTool(it) : it));
  return found ? result : null;
}

/** Recursively find the Agent tool whose toolUseId matches (top-level OR nested
 * inside another Agent's childTools) and merge a subagent_progress event onto
 * it. Returns the new items array when a matching Agent was found, else null.
 *
 * Constrained to toolName === "Agent" — unlike updateToolInTree (which matches
 * any toolUseId), a subagent_progress event must not attach to a non-Agent tool
 * that happens to share the id (that stays a standalone row; see the
 * "non-Agent same-id" test). The Agent match keeps its own childTools via spread
 * (tool_use_id is globally unique, so no deeper match is needed once found). */
function mergeSubagentIntoTree(
  items: readonly TurnItem[],
  toolUseId: string,
  event: SubagentProgressEvent,
): readonly TurnItem[] | null {
  let found = false;
  const merge = (it: ToolItem): ToolItem => {
    if (it.toolName === "Agent" && it.toolUseId === toolUseId) {
      found = true;
      return { ...it, subagent: mergeSubagent(it.subagent, event) };
    }
    if (it.childTools.length > 0) {
      return { ...it, childTools: it.childTools.map(merge) };
    }
    return it;
  };
  const result = items.map((it) => (it.kind === "tool" ? merge(it) : it));
  return found ? result : null;
}

/** Attach a child tool_use to the parent Agent ToolItem (matched by
 * parent_tool_use_id). Returns null when no parent matches so the caller can
 * fall back to a top-level append (never lose the event). slice-025-a 阶段B. */
function attachChildToParent(
  prev: ReducerState,
  parentToolUseId: string,
  child: ToolItem,
): ReducerState | null {
  const turns = prev.turns;
  if (turns.length === 0) return null;
  const last = turns[turns.length - 1];
  const newItems = updateToolInTree(last.items, parentToolUseId, (parent) => ({
    ...parent,
    childTools: [...parent.childTools, child],
  }));
  if (newItems === null) return null;
  const updatedLast: Turn = { ...last, items: newItems };
  return { ...prev, turns: [...turns.slice(0, -1), updatedLast] };
}

/** Update the ToolItem matching toolUseId anywhere in the current turn's items
 * tree (top-level or nested inside childTools). No match → no-op (return prev).
 * Used by tool_progress/tool_result — tool_use_id is globally unique so no
 * parent field is needed on those events (slice-025-a 阶段B). */
function updateToolInCurrentTurn(
  prev: ReducerState,
  toolUseId: string,
  update: (tool: ToolItem) => ToolItem,
): ReducerState {
  const turns = prev.turns;
  if (turns.length === 0) return prev;
  const last = turns[turns.length - 1];
  const newItems = updateToolInTree(last.items, toolUseId, update);
  if (newItems === null) return prev;
  const updatedLast: Turn = { ...last, items: newItems };
  return { ...prev, turns: [...turns.slice(0, -1), updatedLast] };
}

/** Update the pending ElicitationItem matching toolUseId in the current turn
 * (top-level only — sub-agents don't ask AskUserQuestion). Returns null if no
 * pending elicit matches, so the caller can fall through to the tool path
 * (slice-025-c). */
function updateElicitInCurrentTurn(
  prev: ReducerState,
  toolUseId: string,
  update: (elicit: ElicitationItem) => ElicitationItem,
): ReducerState | null {
  const turns = prev.turns;
  if (turns.length === 0) return null;
  const last = turns[turns.length - 1];
  let found = false;
  const newItems = last.items.map((it) => {
    if (
      it.kind === "elicit" &&
      it.toolUseId === toolUseId &&
      it.status === "pending"
    ) {
      found = true;
      return update(it);
    }
    return it;
  });
  if (!found) return null;
  const updatedLast: Turn = { ...last, items: newItems };
  return { ...prev, turns: [...turns.slice(0, -1), updatedLast] };
}

// ---------------------------------------------------------------------------
// slice-028: V2 tasks (TaskCreate/TaskUpdate) — pure helpers
// ---------------------------------------------------------------------------

/** Match `Task #N created successfully` (the cc TaskCreate result text) and
 * return N as a string, or null when the text doesn't match. */
const TASK_CREATED_RE = /Task\s+#(\d+)\s+created/i;

/** slice-028: maintain the V2 task list from TaskCreate/TaskUpdate tool_calls.
 * Returns prev unchanged when the event isn't a task tool. The ToolItem for the
 * same tool_call is still appended by the caller (the message stream keeps the
 * row, per the slice-028 mockup) — this helper only tends the separate task
 * list. */
function applyTaskToolCall(prev: ReducerState, event: ToolCallEvent): ReducerState {
  if (event.tool_name === "TaskCreate") {
    const input = event.input as {
      subject?: unknown;
      description?: unknown;
      activeForm?: unknown;
    };
    const subject = typeof input.subject === "string" ? input.subject : "";
    const task: Task = {
      taskId: null,
      toolUseId: event.tool_use_id,
      subject,
      description: typeof input.description === "string" ? input.description : undefined,
      activeForm: typeof input.activeForm === "string" ? input.activeForm : undefined,
      status: "pending",
    };
    return { ...prev, tasks: [...prev.tasks, task] };
  }
  if (event.tool_name === "TaskUpdate") {
    const input = event.input as { taskId?: unknown; status?: unknown };
    if (typeof input.taskId !== "string") return prev;
    if (input.status !== "in_progress" && input.status !== "completed") return prev;
    const newStatus: Task["status"] = input.status;
    let found = false;
    const tasks = prev.tasks.map((t) => {
      if (t.taskId === input.taskId) {
        found = true;
        return { ...t, status: newStatus };
      }
      return t;
    });
    return found ? { ...prev, tasks } : prev;
  }
  return prev;
}

/** slice-028: a TaskCreate tool_result carries "Task #N created successfully"
 * — parse N and stamp it onto the matching pending task (matched by tool_use_id
 * while taskId is still null). No-op for non-task results. */
function assignTaskIdFromResult(
  prev: ReducerState,
  toolUseId: string,
  content: string,
): ReducerState {
  if (typeof content !== "string") return prev;
  const m = content.match(TASK_CREATED_RE);
  if (!m) return prev;
  const taskId = m[1];
  let found = false;
  const tasks = prev.tasks.map((t) => {
    if (t.toolUseId === toolUseId && t.taskId === null) {
      found = true;
      return { ...t, taskId };
    }
    return t;
  });
  return found ? { ...prev, tasks } : prev;
}

/** Reduce one trowel event into a new ReducerState. Pure. */
export function reduceEvent(prev: ReducerState, event: TrowelEvent): ReducerState {
  // Any non-stall-warning event means cc is alive again — clear the heads-up
  // before running the event's own case. Immutably: we never mutate the
  // incoming prev, this rebinds the local only.
  if (event.type !== "stalled_warning" && prev.meta.stallWarning !== null) {
    prev = { ...prev, meta: { ...prev.meta, stallWarning: null } };
  }
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

    case "turn_start": {
      // slice-026: attach the backend turn_id + revertible flag to the
      // optimistic turn the store already created (live path). No-op when
      // there is no current turn (defensive — shouldn't happen on the live
      // path since send() creates the turn before streaming).
      const turns = prev.turns;
      if (turns.length === 0) return prev;
      const last = turns[turns.length - 1];
      const updatedLast: Turn = {
        ...last,
        turnId: event.turn_id,
        revertible: event.revertible,
      };
      return { ...prev, turns: [...turns.slice(0, -1), updatedLast] };
    }

    case "user": {
      // history-only: start a fresh turn carrying the user's text
      const turn: Turn = {
        id: nextTurnId(),
        userText: event.text,
        items: [],
        status: "active",
        turnId: null,
        revertible: false,
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

    case "thinking_progress": {
      // First heartbeat records the start moment; later heartbeats only refresh
      // the token count. NOTE: Date.now() makes this case non-pure; tests use
      // vi.setSystemTime. See slice-025-a decision #6.
      const startedAt = prev.meta.thinkingStartedAt ?? Date.now();
      return {
        ...prev,
        phase: "thinking",
        meta: {
          ...prev.meta,
          thinkingStartedAt: startedAt,
          thinkingTokens: event.estimated_tokens,
        },
      };
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
      // Stamp the thinking duration onto the new item and clear the heartbeat
      // state. NOTE: Date.now() — non-pure; tests mock.
      // Two sources, in priority order:
      //   1. heartbeat-derived (live): first heartbeat -> now
      //   2. event.thinking_duration_seconds (history replay): history.py
      //      back-filled it from entry-timestamp deltas
      // Both fall through to `undefined` when unavailable, which makes
      // EventTimeline fall back to a bare "思考" label.
      const startedAt = prev.meta.thinkingStartedAt;
      const duration =
        startedAt !== null
          ? Math.max(1, Math.round((Date.now() - startedAt) / 1000))
          : event.thinking_duration_seconds;
      return appendToCurrentTurn(
        {
          ...prev,
          phase: "thinking",
          meta: { ...prev.meta, thinkingStartedAt: null, thinkingTokens: null },
        },
        {
          kind: "thinking",
          text: event.text,
          thinkingDurationSeconds: duration,
        },
      );
    }

    case "tool_call": {
      const newItem: ToolItem = {
        kind: "tool",
        toolUseId: event.tool_use_id,
        toolName: event.tool_name,
        input: event.input,
        status: "running",
        elapsedSeconds: null,
        result: null,
        childTools: [],
        // writeDiff arrives on the tool_result (slice-033 feat 2 方案 F), not
        // here — cc computes the patch at execution time.
      };
      // slice-028: TaskCreate/TaskUpdate also maintain the session task list
      // (the ToolItem above is still appended so the message stream keeps the
      // row; tasks are a separate, session-scoped list for the todo bar).
      const withTasks = applyTaskToolCall(prev, event);
      const parentId = event.parent_tool_use_id;
      if (parentId) {
        const attached = attachChildToParent(withTasks, parentId, newItem);
        if (attached !== null) return { ...attached, phase: "tool" };
      }
      return appendToCurrentTurn({ ...withTasks, phase: "tool" }, newItem);
    }

    case "tool_progress":
      return {
        ...updateToolInCurrentTurn(prev, event.tool_use_id, (t) => ({
          ...t,
          elapsedSeconds: event.elapsed_time_seconds,
        })),
        phase: "tool",
      };

    case "tool_result": {
      // slice-025-c: elicit completion path — if a pending ElicitationItem
      // matches this tool_use_id, flip it to answered (cc's tool_result text
      // is "User has answered..." which we echo in the completed state).
      const withElicit = updateElicitInCurrentTurn(
        prev,
        event.tool_use_id,
        (e) => ({
          ...e,
          status: "answered" as const,
          resultText: event.content,
        }),
      );
      if (withElicit !== null) {
        return { ...withElicit, phase: "tool" };
      }
      // slice-028: a TaskCreate result carries "Task #N created successfully"
      // — stamp N onto the matching pending task before the tool path runs.
      const afterTask = assignTaskIdFromResult(
        prev,
        event.tool_use_id,
        event.content,
      );
      return {
        ...updateToolInCurrentTurn(afterTask, event.tool_use_id, (t) => ({
          ...t,
          status: "done",
          result: event.content,
          // slice-033 feat 2 (方案 F): BE attaches cc's own structuredPatch
          // (real file line numbers) to Edit/MultiEdit/Write tool_results.
          // Keep any prior writeDiff as fallback (none in practice — tool_call
          // doesn't set one anymore) when this result carries none.
          writeDiff: event.write_diff ?? t.writeDiff,
        })),
        phase: "tool",
      };
    }

    case "elicit_request": {
      const item: ElicitationItem = {
        kind: "elicit",
        toolUseId: event.tool_use_id,
        requestId: event.request_id,
        questions: event.questions,
        status: "pending",
        resultText: null,
        answers: null,
      };
      return {
        ...appendToCurrentTurn(prev, item),
        phase: "awaiting_input",
      };
    }

    case "subagent_progress": {
      // Attach to the Agent ToolItem whose tool_use_id matches (merge fields;
      // started's description/subagent_type survive into progress/completed).
      // If no Agent tool matches, append a standalone subagent item (decision #10).
      // Drop events with no tool_use_id (malformed — task_started always has
      // one) so they don't mis-attach to an empty-id tool.
      if (!event.tool_use_id) return prev;
      const turns = prev.turns;
      if (turns.length === 0) return prev;
      const last = turns[turns.length - 1];
      // 递归在整个 items 树（含嵌套 Agent 的 childTools）里找匹配的 Agent tool
      // 合并进度。之前只扫顶层 items：subagent 调 subagent 时，内层 Agent 嵌在
      // 父 Agent 的 childTools 里，其进度事件匹配不到 → 整条进度流溢出成顶层
      // standalone 块（实测一次嵌套调用撑出 313 个平铺 subagent，每次
      // last_tool_name 更新都新加一行）。
      const merged = mergeSubagentIntoTree(
        last.items,
        event.tool_use_id,
        event,
      );
      if (merged !== null) {
        const updatedLast: Turn = { ...last, items: merged };
        return { ...prev, turns: [...turns.slice(0, -1), updatedLast] };
      }
      // slice-036: workflow subagent 的 task_* 事件 tool_use_id 指向 workflow
      // 内部（无顶层 Agent tool_use），走不到上面的合并分支。若 session 任意
      // turn 已有 workflow item，说明它是 workflow subagent——已由 WorkflowTree
      // 渲染，丢弃避免溢出 SubagentBlock（实测 141 个 standalone 的根因）。
      // 无 workflow 时保留 standalone SubagentItem（slice-025-a decision #10）。
      if (turns.some((t) => t.items.some((it) => it.kind === "workflow"))) {
        return prev;
      }
      return appendToCurrentTurn(prev, {
        kind: "subagent",
        toolUseId: event.tool_use_id,
        subagent: mergeSubagent(undefined, event),
      });
    }

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

    case "stalled_warning":
      // Phased heads-up — does NOT change phase (cc is still running, just
      // silent). Stored on meta so the spinner overlay can render the warning;
      // any subsequent non-stall-warning event clears it (see reduceEvent entry).
      return {
        ...prev,
        meta: {
          ...prev.meta,
          stallWarning: {
            severity: event.severity,
            elapsed_s: event.elapsed_s,
          },
        },
      };

    case "model_changed": {
      // slice-027 C2: immediate StatusBar sync. CC is lazy-restarted by the
      // next send, so the actual --model flag change comes later; this event
      // updates meta.model now so the display doesn't lag a turn behind.
      // event.effort lives in zustand (set in apply), not ReducerState.
      const nextModel = event.model ?? prev.meta.model;
      if (nextModel === prev.meta.model) return prev; // no-op → no rerender
      return { ...prev, meta: { ...prev.meta, model: nextModel } };
    }

    case "session_exited":
      // slice-028 bug3: the CC subprocess exited. Mark the session lifecycle
      // flag; the shell unsets activeSid if this was the active session so the
      // view returns to the no-active-session state. Turns/tasks are preserved
      // (the user can re-activate the row to view them; sending respawns cc).
      //
      // NOTE: on the live path the zustand SHELL (ccStore.applyTo) deletes the
      // session from the dict BEFORE the event reaches this reducer, so this
      // case only fires for history replay / direct reducer unit tests. Kept
      // for completeness; do not rely on it for the live multi-session flow.
      return {
        ...prev,
        meta: {
          ...prev.meta,
          exited: true,
          exitReturncode: event.returncode,
        },
      };

    case "workflow_tree": {
      // slice-036: a full workflow snapshot. Replace the prior snapshot
      // matched by runId (live watcher re-emits as cc rewrites wf.json), or
      // append to the current turn if none exists yet. Scanned across ALL
      // turns because a workflow routinely outlives its launch turn — cc
      // backgrounds it and the final-state snapshot may land on a later turn.
      return upsertWorkflowItem(prev, workflowItemFromEvent(event));
    }

    default:
      return prev;
  }
}

/** Build a WorkflowItem from a wire WorkflowTreeEvent (snake→camel). */
function workflowItemFromEvent(event: WorkflowTreeEvent): WorkflowItem {
  return {
    kind: "workflow",
    runId: event.run_id,
    taskId: event.task_id,
    name: event.name,
    args: event.args,
    status: event.status,
    agentCount: event.agent_count,
    doneCount: event.done_count,
    totalTokens: event.total_tokens,
    totalToolCalls: event.total_tool_calls,
    durationMs: event.duration_ms,
    phases: event.phases,
    agents: event.agents,
    error: event.error,
  };
}

/** Replace the workflow item whose runId matches (anywhere in the turn tree),
 * else append it to the last turn. slice-036. */
function upsertWorkflowItem(prev: ReducerState, item: WorkflowItem): ReducerState {
  const turns = prev.turns;
  if (turns.length === 0) return prev;
  let found = false;
  const newTurns = turns.map((t) => {
    if (found) return t;
    let hit = false;
    const items = t.items.map((it) => {
      if (it.kind === "workflow" && it.runId === item.runId) {
        hit = true;
        return item;
      }
      return it;
    });
    if (!hit) return t;
    found = true;
    return { ...t, items };
  });
  if (found) return { ...prev, turns: newTurns };
  const last = turns[turns.length - 1];
  const updatedLast: Turn = { ...last, items: [...last.items, item] };
  return { ...prev, turns: [...turns.slice(0, -1), updatedLast] };
}

/** Merge a subagent_progress event onto the prior SubagentState; fields absent
 * on the event (undefined) fall back to the previous value, so the started
 * event's description/subagent_type survive into progress/completed updates. */
function mergeSubagent(
  prev: SubagentState | undefined,
  event: SubagentProgressEvent,
): SubagentState {
  return {
    status: event.status,
    description: event.description ?? prev?.description ?? null,
    subagent_type: event.subagent_type ?? prev?.subagent_type ?? null,
    last_tool_name: event.last_tool_name ?? prev?.last_tool_name ?? null,
    usage: event.usage ?? prev?.usage ?? null,
  };
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

/**
 * End an still-active turn when the live SSE stream closes with no terminal
 * event (finished/error/...).
 *
 * The host's slash-command paths — /model, /effort (RestartSession → one
 * StatusEvent), /cost, /status (LocalCommand), and unsupported slashes — each
 * emit a single status/local_command event then close the stream; CC is never
 * spawned, so no `finished` ever arrives. The reducer only ends a turn on a
 * terminal event, so without this the composer would stay stuck in "生成中"
 * forever after any slash command.
 *
 * Only a CLEAN close ends the turn: a transport failure is left for the error
 * UI, and a user abort is handled by the interrupt path. `meta` (incl.
 * costUsd) is never touched — no synthetic finished — so /cost's real value
 * survives.
 */
export function endActiveTurnOnStreamClose(
  state: ReducerState,
  opts: { aborted: boolean; transportOk: boolean },
): ReducerState {
  if (!opts.transportOk || opts.aborted) {
    return state;
  }
  const last = state.turns[state.turns.length - 1];
  if (!last || last.status !== "active") {
    return state;
  }
  const lastIdx = state.turns.length - 1;
  const turns = state.turns.map((t, i) =>
    i === lastIdx ? { ...t, status: "done" as const } : t,
  );
  return { ...state, turns, phase: "done" };
}

/** In-progress phases that flip to "done" when finalizing a history view. */
const _ACTIVE_PHASES: ReadonlySet<Phase> = new Set([
  "awaiting_first",
  "thinking",
  "generating",
  "tool",
  "retrying",
  "compacting",
]);

/**
 * Finalize replayed history into a restful "past session" state.
 *
 * CC's persisted jsonl has no `result` line, so history replay never produces
 * a `finished` event — every past turn would stay "active" and the phase would
 * stay "generating", which disables the composer (the user could not continue
 * a loaded session). This flips active turns to done and an in-progress phase
 * to done. Terminal statuses (error / interrupted) are preserved as-is.
 */
export function finalizeHistoryForView(state: ReducerState): ReducerState {
  const turns = state.turns.map((t) =>
    t.status === "active" ? { ...t, status: "done" as const } : t,
  );
  const phase: Phase = _ACTIVE_PHASES.has(state.phase) ? "done" : state.phase;
  return { ...state, turns, phase };
}
