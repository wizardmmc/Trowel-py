import type { AgentPendingRequest } from "../../api/agent";
import type { ApprovalRequestEvent } from "../../api/ccTypes";
import { reduceEvent } from "../ccReducer";
import type { PerSessionState } from "./sessionState";

/** 把 REST 恢复的 approval 请求折叠到 SSE 共用的 reducer。 */
export function applyPendingApproval(
  session: PerSessionState,
  request: AgentPendingRequest,
): PerSessionState {
  const currentTurn = session.turns[session.turns.length - 1];
  const belongsToCurrentTurn =
    request.turn_id === null ||
    currentTurn?.turnId === request.turn_id ||
    currentTurn?.items.some(
      (item) =>
        item.kind === "approval" && item.requestId === request.request_id,
    ) === true;
  const reduced = reduceEvent(session, approvalEventFrom(request));

  return {
    ...session,
    ...reduced,
    // 恢复旧 turn 的请求不能改变当前 turn 的输入状态。
    phase: belongsToCurrentTurn ? reduced.phase : session.phase,
  };
}

function approvalEventFrom(
  request: AgentPendingRequest,
): ApprovalRequestEvent {
  return {
    type: "approval_request",
    turn_id: request.turn_id ?? undefined,
    request_id: request.request_id,
    item_id: request.item_id,
    approval_kind: request.approval_kind,
    command: request.command,
    cwd: request.cwd,
    reason: request.reason,
    available_decisions: request.available_decisions,
    status: request.status,
    decision: request.decision,
    auto_resolved: request.auto_resolved,
    resolution_reason: request.resolution_reason,
  };
}
