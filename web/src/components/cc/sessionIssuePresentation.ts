/** 把正交会话状态和 transport problem 投影成去重后的用户可见问题。 */

import {
  isRootTurnInFlight,
  type PerSessionState,
} from "../../agent/application";
import type { RuntimePresentation } from "../../agent/runtimes";

export type SessionIssueTone = "error" | "warning" | "info";
export type SessionIssueRole = "alert" | "status";

export interface SessionIssue {
  readonly id: string;
  readonly title: string;
  readonly detail: string;
  readonly tone: SessionIssueTone;
  readonly role: SessionIssueRole;
}

export function collectSessionIssues(
  active: PerSessionState,
  activeSid: string | null,
  presentation: RuntimePresentation,
): readonly SessionIssue[] {
  const issues: SessionIssue[] = [];
  const code = active.transportProblem?.code ?? null;
  let transportProblemMerged = false;

  if (active.resourceState === "needs_reconcile") {
    issues.push({
      id: "resource",
      title: "会话关闭未完成",
      detail: isRootTurnInFlight(active)
        ? "运行时进程仍未退出，资源状态需要继续对账。发送暂不可用，可以安全地重试关闭。"
        : "会话已停止，但运行时资源仍待清理。可以安全地重试关闭。",
      tone: "error",
      role: "alert",
    });
    transportProblemMerged = code === "close_needs_reconcile";
  }

  if (active.liveState === "reconnecting") {
    issues.push({
      id: "live",
      title: "实时连接恢复中",
      detail: "正在用后端快照对账，不会自动重发上一条消息。",
      tone: "warning",
      role: "status",
    });
    transportProblemMerged =
      transportProblemMerged ||
      code === "live_disconnected" ||
      code === "live_unavailable";
  }

  if (
    active.resourceState !== "needs_reconcile" &&
    active.liveState !== "reconnecting" &&
    (active.liveState === "gapped" || active.turnState === "unknown")
  ) {
    const acceptanceUnknown = code === "turn_acceptance_unknown";
    issues.push({
      id: "turn",
      title: acceptanceUnknown ? "发送结果尚未确认" : "会话状态待对账",
      detail: acceptanceUnknown
        ? "实时事件出现缺口，正在与后端对账。发送暂不可用，请勿重发上一条消息。"
        : "实时事件存在缺口，发送入口保持关闭，直到后端快照确认当前 turn。",
      tone: "warning",
      role: "alert",
    });
    transportProblemMerged =
      acceptanceUnknown ||
      code === "event_gap" ||
      code === "turn_state_unknown";
  }

  if (
    (active.transportError || active.transportProblem) &&
    !transportProblemMerged
  ) {
    issues.push({
      id: "transport",
      title: "Agent 操作失败",
      detail: safeTransportProblemDetail(active),
      tone: "error",
      role: "alert",
    });
  }

  if (active.meta.hostDegraded && presentation.headerStatus.degradedHostLabel) {
    issues.push({
      id: "host",
      title: presentation.headerStatus.degradedHostLabel,
      detail:
        "运行中的轮次已按出错收口；空闲会话可在重连后恢复，不会自动重放写操作。",
      tone: "error",
      role: "alert",
    });
  }

  if (
    activeSid &&
    presentation.supports("checkpoint") &&
    active.checkpointAvailable === false
  ) {
    issues.push({
      id: "checkpoint-unavailable",
      title: "当前无法创建新的回滚点",
      detail: "已有回滚点仍可使用。",
      tone: "warning",
      role: "status",
    });
  }

  if (
    activeSid &&
    presentation.supports("checkpoint") &&
    active.checkpointAvailable === null
  ) {
    issues.push({
      id: "checkpoint-unknown",
      title: "回滚可用性尚未确认",
      detail: "已有会话仍按每轮记录决定是否显示回滚入口。",
      tone: "warning",
      role: "status",
    });
  }

  if (presentation.missingCapabilities.length > 0) {
    issues.push({
      id: "capabilities",
      title: "Runtime capability 信息不完整",
      detail: `部分设置或专属展示已隐藏：${presentation.missingCapabilities.join("、")}`,
      tone: "info",
      role: "status",
    });
  }

  return issues;
}

/** 把不受信任的后端错误正文收敛为不含路径、会话身份或 prompt 的稳定文案。 */
function safeTransportProblemDetail(active: PerSessionState): string {
  const problem = active.transportProblem;
  if (!problem) {
    return "操作未完成。请先确认当前会话状态，再决定是否重试。";
  }
  if (problem.code === "sidecar_unavailable") {
    return "Agent Service 已断开。Desktop Host 会转入诊断页；写操作不会自动重发。";
  }
  if (problem.code === "request_timeout") {
    return "操作超过等待时间，结果可能尚未确认。请先检查当前事实。";
  }
  if (problem.code === "http_error") {
    return problem.status === null
      ? "Agent 返回错误。请按技术详情中的安全动作处理。"
      : `Agent 返回错误（HTTP ${problem.status}）。请按技术详情中的安全动作处理。`;
  }
  return "操作未完成。请按技术详情中的问题类型和安全动作处理。";
}

export function safeSessionAction(code: string): string {
  if (code === "close_needs_reconcile") return "只重试关闭";
  if (code === "turn_acceptance_unknown") {
    return "等待状态对账，不要重发上一条消息";
  }
  if (code === "event_gap" || code === "turn_state_unknown") {
    return "等待历史与快照恢复";
  }
  if (code === "live_disconnected" || code === "live_unavailable") {
    return "等待实时连接恢复";
  }
  if (code === "sidecar_unavailable") {
    return "按 Desktop 诊断重启 Agent Service，不要重发上一条写操作";
  }
  return "只读操作可以重试；写操作先确认后端事实";
}
