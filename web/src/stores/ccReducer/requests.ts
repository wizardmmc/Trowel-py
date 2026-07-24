import type {
  ApprovalRequestEvent,
  ElicitationRequestEvent,
} from "../../api/ccTypes";
import type {
  ApprovalItem,
  ElicitationItem,
  Phase,
  ReducerState,
  Turn,
} from "./model";

/** 匹配当前 turn 中仍待回答的 elicitation。 */
export function resolveElicitationResult(
  prev: ReducerState,
  toolUseId: string,
  resultText: string | null,
): ReducerState | null {
  const turns = prev.turns;
  if (turns.length === 0) return null;

  const last = turns[turns.length - 1];
  let found = false;
  const items = last.items.map((item) => {
    if (
      item.kind !== "elicit" ||
      item.toolUseId !== toolUseId ||
      item.status !== "pending"
    ) {
      return item;
    }
    found = true;
    return {
      ...item,
      status: "answered" as const,
      resultText,
    };
  });
  if (!found) return null;

  const updated: Turn = { ...last, items };
  return { ...prev, turns: [...turns.slice(0, -1), updated] };
}

export function applyElicitationRequest(
  prev: ReducerState,
  event: ElicitationRequestEvent,
): ReducerState {
  const item: ElicitationItem = {
    kind: "elicit",
    toolUseId: event.tool_use_id,
    requestId: event.request_id,
    questions: event.questions,
    status: "pending",
    resultText: null,
    answers: null,
  };

  const turns = prev.turns;
  if (turns.length === 0) {
    return { ...prev, phase: "awaiting_input" };
  }
  const last = turns[turns.length - 1];
  const updated: Turn = { ...last, items: [...last.items, item] };
  return {
    ...prev,
    phase: "awaiting_input",
    turns: [...turns.slice(0, -1), updated],
  };
}

function upsertApproval(
  prev: ReducerState,
  approval: ApprovalItem,
): ReducerState {
  const turns = prev.turns;
  if (turns.length === 0) return prev;

  const existingTurnIndex = turns.findIndex((turn) =>
    turn.items.some(
      (item) =>
        item.kind === "approval" && item.requestId === approval.requestId,
    ),
  );
  const matchingTurnIndex =
    approval.turnId === null
      ? -1
      : turns.findIndex((turn) => turn.turnId === approval.turnId);
  const turnIndex =
    existingTurnIndex >= 0
      ? existingTurnIndex
      : matchingTurnIndex >= 0
        ? matchingTurnIndex
        : turns.length - 1;

  const target = turns[turnIndex];
  const approvalIndex = target.items.findIndex(
    (item) =>
      item.kind === "approval" && item.requestId === approval.requestId,
  );
  const items =
    approvalIndex === -1
      ? [...target.items, approval]
      : target.items.map((item, index) =>
          index === approvalIndex ? approval : item,
        );
  const updated: Turn = { ...target, items };
  return {
    ...prev,
    turns: turns.map((turn, index) =>
      index === turnIndex ? updated : turn,
    ),
  };
}

export function applyApprovalRequest(
  prev: ReducerState,
  event: ApprovalRequestEvent,
): ReducerState {
  const approval: ApprovalItem = {
    kind: "approval",
    requestId: event.request_id,
    turnId: event.turn_id ?? null,
    itemId: event.item_id,
    approvalKind: event.approval_kind,
    command: event.command,
    cwd: event.cwd,
    reason: event.reason,
    availableDecisions: event.available_decisions,
    status: event.status,
    decision: event.decision,
    autoResolved: event.auto_resolved,
    resolutionReason: event.resolution_reason,
  };
  const phase: Phase =
    event.status === "pending"
      ? "awaiting_input"
      : event.status === "host_closed"
        ? "error"
        : "tool";
  return { ...upsertApproval(prev, approval), phase };
}
