/** 生成不含会话 ID、工作目录和提示词的会话恢复诊断。 */

import type { PerSessionState } from "./store/sessionState";
import { copyText } from "../../lib/copyText";

export interface SessionDiagnosticContext {
  readonly visibleIssueIds?: readonly string[];
  readonly missingCapabilities?: readonly string[];
}

/** 把恢复所需事实编码成可复制文本，不泄露用户内容或本机路径。 */
export function buildSessionDiagnostic(
  session: PerSessionState,
  context: SessionDiagnosticContext = {},
): string {
  const problem = session.transportProblem;
  return JSON.stringify(
    {
      schema: "agent-session-diagnostic-v1",
      runtime: session.runtime,
      resource_state: session.resourceState,
      turn_state: session.turnState,
      phase: session.phase,
      live_state: session.liveState,
      state_generation: session.stateGeneration,
      last_event_seq: session.lastSeq,
      needs_replay: session.needsReplay,
      checkpoint_available: session.checkpointAvailable,
      host_degraded: Boolean(session.meta.hostDegraded),
      visible_issue_ids: [...(context.visibleIssueIds ?? [])].sort(),
      capabilities: {
        declared: [...session.capabilities].sort(),
        missing: [...(context.missingCapabilities ?? [])].sort(),
      },
      problem: problem
        ? {
            code: problem.code,
            operation: problem.operation,
            budget_ms: problem.budgetMs,
            status: problem.status,
            occurred_at: problem.occurredAt,
          }
        : null,
    },
    null,
    2,
  );
}

/** 复制脱敏诊断，并让调用方只在成功后展示反馈。 */
export async function copySessionDiagnostic(
  session: PerSessionState,
  context?: SessionDiagnosticContext,
): Promise<boolean> {
  try {
    await copyText(buildSessionDiagnostic(session, context));
    return true;
  } catch {
    return false;
  }
}
