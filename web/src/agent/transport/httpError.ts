/** 从本地 Agent Service 错误中提取稳定、可判断且可直接展示的问题。 */

export type AgentProblemCode =
  | "sidecar_unavailable"
  | "request_timeout"
  | "live_unavailable"
  | "live_disconnected"
  | "event_gap"
  | "turn_acceptance_unknown"
  | "close_needs_reconcile"
  | "turn_state_unknown"
  | "http_error";

export interface AgentTransportProblem {
  readonly code: AgentProblemCode;
  readonly message: string;
  readonly operation: string;
  readonly budgetMs: number | null;
  readonly status: number | null;
  readonly occurredAt: string;
}

/** 携带稳定问题码的 transport 异常，界面可以展示 message 并按 code 决策。 */
export class AgentTransportError extends Error {
  readonly problem: AgentTransportProblem;

  constructor(problem: AgentTransportProblem, options?: ErrorOptions) {
    super(problem.message, options);
    this.name = "AgentTransportError";
    this.problem = problem;
  }
}

/** 把任意异常收敛成 UI 可展示、可复制的稳定问题。 */
export function agentProblemFromUnknown(
  error: unknown,
  fallback: Omit<AgentTransportProblem, "message" | "status" | "occurredAt">,
): AgentTransportProblem {
  if (error instanceof AgentTransportError) return error.problem;
  return {
    ...fallback,
    message: error instanceof Error ? error.message : String(error),
    status: null,
    occurredAt: new Date().toISOString(),
  };
}

/** 优先读取结构化错误字段，无法解析时回退到带状态码的稳定说明。 */
export async function readHttpError(
  response: Response,
  fallbackPrefix: string,
): Promise<string> {
  const problem = await readHttpProblem(
    response,
    fallbackPrefix,
    "agent_request",
    null,
  );
  return problem.message;
}

/** 读取后端稳定问题码及其操作、预算元数据。 */
export async function readHttpProblem(
  response: Response,
  fallbackPrefix: string,
  fallbackOperation: string,
  fallbackBudgetMs: number | null,
): Promise<AgentTransportProblem> {
  let code: AgentProblemCode = "http_error";
  let message = `${fallbackPrefix}: ${response.status}`;
  let operation = fallbackOperation;
  let budgetMs = fallbackBudgetMs;
  try {
    const body: unknown = await response.json();
    if (body && typeof body === "object") {
      const payload = body as Record<string, unknown>;
      const meta = payload.meta;
      if (meta && typeof meta === "object") {
        const values = meta as Record<string, unknown>;
        if (typeof values.operation === "string") operation = values.operation;
        if (typeof values.timeout_ms === "number") budgetMs = values.timeout_ms;
      }
      for (const key of ["error", "detail"] as const) {
        const value = payload[key];
        if (typeof value === "string" && value.trim()) {
          message = value;
          break;
        }
        if (value && typeof value === "object") {
          const values = value as Record<string, unknown>;
          if (typeof values.message === "string" && values.message.trim()) {
            message = values.message;
          }
          if (isAgentProblemCode(values.code)) code = values.code;
          break;
        }
      }
    }
  } catch {
    // 非 JSON 错误页仍由下面的 HTTP 状态说明兜底。
  }
  return {
    code,
    message,
    operation,
    budgetMs,
    status: response.status,
    occurredAt: new Date().toISOString(),
  };
}

function isAgentProblemCode(value: unknown): value is AgentProblemCode {
  return (
    typeof value === "string" &&
    [
      "sidecar_unavailable",
      "request_timeout",
      "live_unavailable",
      "live_disconnected",
      "event_gap",
      "turn_acceptance_unknown",
      "close_needs_reconcile",
      "turn_state_unknown",
      "http_error",
    ].includes(value)
  );
}
