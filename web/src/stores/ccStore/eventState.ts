import type { AgentEvent } from "../../api/agentTypes";
import { agentEventToTrowel } from "../../api/agentTypes";
import { reduceEvent } from "../ccReducer";
import type { PerSessionState } from "./sessionState";

export type AgentEventReduction =
  | { readonly kind: "duplicate" }
  | { readonly kind: "session_exited" }
  | { readonly kind: "updated"; readonly session: PerSessionState };

interface AgentEventReductionOptions {
  readonly acceptRequestErrorSeqReset?: boolean;
}

/** 把一个带 seq 的统一事件归约到单个会话，不处理 Zustand 字典编排。 */
export function reduceAgentEvent(
  current: PerSessionState,
  event: AgentEvent,
  options: AgentEventReductionOptions = {},
): AgentEventReduction {
  const activeTurn = current.turns.at(-1)?.status === "active";
  const requestErrorSeqReset =
    options.acceptRequestErrorSeqReset === true &&
    activeTurn &&
    event.type === "error" &&
    current.lastSeq !== null &&
    event.seq <= current.lastSeq;
  if (
    current.lastSeq !== null &&
    event.seq <= current.lastSeq &&
    !requestErrorSeqReset
  ) {
    return { kind: "duplicate" };
  }
  const baseline = requestErrorSeqReset ? { ...current, lastSeq: null } : current;
  const gapped =
    baseline.lastSeq !== null && event.seq > baseline.lastSeq + 1;

  // Claude Code 进程退出会删除连接行；Codex host_exited 仍保留绑定。
  if (event.type === "session_exited") {
    return { kind: "session_exited" };
  }

  const flat = agentEventToTrowel(event);
  const reduced = reduceEvent(baseline, flat);
  let next: PerSessionState = {
    ...baseline,
    ...reduced,
    lastSeq: event.seq,
    needsReplay: baseline.needsReplay || gapped,
  };

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
