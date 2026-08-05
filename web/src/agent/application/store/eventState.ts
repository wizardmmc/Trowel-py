/** 归约带序号的实时 Agent 事件，并标记重复、缺口和会话退出。 */

import type { AgentEvent } from "../../transport/agentEvent";
import { agentEventToTrowel } from "../../transport/agentEvent";
import { reduceEvent } from "../../domain/reducer";
import type { PerSessionState } from "./sessionState";
import {
  isUnknownCodexChildEvent,
  reduceCodexSubagentEvent,
} from "./codexSubagents";

export type AgentEventReduction =
  | { readonly kind: "duplicate" }
  | { readonly kind: "session_exited" }
  | { readonly kind: "updated"; readonly session: PerSessionState };

/** 把一个带 seq 的统一事件归约到单个会话，不处理 Zustand 字典编排。 */
export function reduceAgentEvent(
  current: PerSessionState,
  event: AgentEvent,
): AgentEventReduction {
  if (
    current.lastSeq !== null &&
    event.seq <= current.lastSeq
  ) {
    return { kind: "duplicate" };
  }
  const baseline = current;
  const gapped =
    baseline.lastSeq !== null && event.seq > baseline.lastSeq + 1;

  // Claude Code 进程退出会删除连接行；Codex host_exited 仍保留绑定。
  if (event.type === "session_exited") {
    return { kind: "session_exited" };
  }

  if (isUnknownCodexChildEvent(baseline, event)) {
    return {
      kind: "updated",
      session: {
        ...baseline,
        lastSeq: event.seq,
        needsReplay: true,
        liveState: "gapped",
        turnState: "unknown",
      },
    };
  }

  const childReduced = reduceCodexSubagentEvent(baseline, event);
  if (childReduced !== null) {
    return {
      kind: "updated",
      session: {
        ...childReduced,
        lastSeq: event.seq,
        needsReplay: baseline.needsReplay || gapped,
        liveState: gapped ? "gapped" : childReduced.liveState,
        turnState: gapped ? "unknown" : childReduced.turnState,
      },
    };
  }

  const rootTerminal = isRootTerminal(baseline, event);
  const terminalMatches =
    !rootTerminal ||
    (!baseline.needsReplay &&
      !gapped &&
      event.turn_id !== null &&
      event.turn_id === baseline.currentTurnId);
  if (rootTerminal && !terminalMatches) {
    return {
      kind: "updated",
      session: {
        ...baseline,
        lastSeq: event.seq,
        needsReplay: true,
        liveState: "gapped",
        turnState: "unknown",
      },
    };
  }

  const flat = agentEventToTrowel(event);
  const reduced = reduceEvent(baseline, flat);
  let next: PerSessionState = {
    ...baseline,
    ...reduced,
    lastSeq: event.seq,
    needsReplay: baseline.needsReplay || gapped,
    liveState: gapped ? "gapped" : baseline.liveState,
    turnState: gapped ? "unknown" : baseline.turnState,
  };

  if (event.type === "turn_start") {
    next = {
      ...next,
      currentTurnId: event.turn_id,
      turnState: event.turn_id === null ? "unknown" : "running",
      abort: next.abort ?? new AbortController(),
      commandPending: null,
    };
  } else if (rootTerminal) {
    const terminalState = {
      finished: "completed",
      interrupted: "interrupted",
      error: "failed",
    } as const;
    next = {
      ...next,
      turnState: terminalState[event.type],
      abort: null,
      commandPending: null,
    };
  } else if (
    event.type === "elicit_request" ||
    (event.type === "approval_request" && event.payload.status === "pending")
  ) {
    next = { ...next, turnState: "awaiting_input" };
  } else if (
    event.type === "host_status" &&
    event.payload.status === "host_exited"
  ) {
    next = {
      ...next,
      turnState: "failed",
      abort: null,
      commandPending: null,
    };
  }

  const effort = (flat as { effort?: string | null }).effort;
  if (event.type === "model_changed" && effort != null) {
    next = {
      ...next,
      effort,
      pendingModel: null,
      pendingEffort: null,
      settingsNotice: null,
    };
  }

  if (event.type === "session_started") {
    const nativeSessionId = event.payload.cc_session_id;
    if (typeof nativeSessionId === "string" && nativeSessionId) {
      next = { ...next, nativeSessionId };
    }
  }

  if (event.type === "session_started" && event.runtime === "codex") {
    const profile = event.payload.permission_profile;
    const sandbox = event.payload.effective_sandbox;
    const approval = event.payload.effective_approval;
    const network = event.payload.network_access;
    const effectiveSandbox = typeof sandbox === "string" ? sandbox : null;
    const effectiveApproval = typeof approval === "string" ? approval : null;
    next = {
      ...next,
      permission: codexPermissionLabel(effectiveSandbox, effectiveApproval),
      effectivePermissionProfile:
        typeof profile === "string" ? profile : null,
      effectiveSandbox,
      effectiveApproval,
      networkAccess: typeof network === "boolean" ? network : null,
    };
  }

  return { kind: "updated", session: next };
}

function isRootTerminal(
  session: PerSessionState,
  event: AgentEvent,
): event is AgentEvent & { readonly type: "finished" | "interrupted" | "error" } {
  if (
    event.type !== "finished" &&
    event.type !== "interrupted" &&
    event.type !== "error"
  ) {
    return false;
  }
  if (event.runtime === "claude_code") return event.thread_id === null;
  return (
    session.nativeSessionId !== null &&
    event.thread_id === session.nativeSessionId
  );
}

function codexPermissionLabel(
  sandbox: string | null,
  approval: string | null,
): string | null {
  if (sandbox === null && approval === null) return null;
  const labels: Readonly<Record<string, string>> = {
    "read-only": "Read only",
    "workspace-write": "Workspace write",
    "danger-full-access": "Full access",
  };
  return `${labels[sandbox ?? ""] ?? sandbox ?? "Unknown sandbox"} · ${approval ?? "unknown approval"}`;
}
