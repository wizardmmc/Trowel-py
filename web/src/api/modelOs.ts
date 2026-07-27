import { postMessageStream } from "./ccStream";

const MODEL_OS_API_BASE = "/api/model-os";

export interface JournalBoundary {
  readonly event_seq: number;
  readonly decision_seq: number;
}

export interface WorkbenchWaiting {
  readonly kind: string;
  readonly cause: string;
  readonly subtype: string | null;
  readonly episode_id: string | null;
  readonly correlation_id: string | null;
  readonly deadline: string | null;
  readonly condition_kind: string | null;
  readonly target_ref: string | null;
  readonly open_question: string | null;
  readonly earliest_review_at: string | null;
}

export interface WorkbenchPendingQuestion {
  readonly question: string;
  readonly header?: string;
  readonly options?: readonly {
    readonly label: string;
    readonly description?: string;
  }[];
  readonly multiSelect?: boolean;
}

export interface WorkbenchPendingRequest {
  readonly kind: "input" | "approval";
  readonly request_id: string;
  readonly prompt: string | null;
  readonly questions: readonly WorkbenchPendingQuestion[];
  readonly available_decisions: readonly (
    | string
    | Readonly<Record<string, unknown>>
  )[];
}

export interface WorkbenchTask {
  readonly task_id: string;
  readonly goal: string;
  readonly constraints: readonly string[];
  readonly status: string;
  readonly priority: number;
  readonly warm: boolean;
  readonly warm_rank: number | null;
  readonly is_foreground: boolean;
  readonly waiting: WorkbenchWaiting | null;
  readonly pending_request?: WorkbenchPendingRequest | null;
  readonly episode_id: string | null;
  readonly episode_status: string | null;
  readonly agent_session_id: string | null;
  readonly runtime: string | null;
  readonly model: string | null;
  readonly effort: string | null;
  readonly connected: boolean | null;
  readonly running: boolean | null;
  readonly can_send_message: boolean;
  readonly current_judgment: string | null;
  readonly next_steps: readonly string[];
  readonly updated_at: string;
}

export interface WorkbenchCandidate {
  readonly candidate_id: string;
  readonly source_kind: "default" | "incubation";
  readonly task_id: string | null;
  readonly title: string;
  readonly related_question: string | null;
  readonly source_refs: readonly string[];
  readonly why_useful: string | null;
  readonly new_points: readonly string[];
  readonly verification: string | null;
  readonly uncertainty: string | null;
  readonly status: string;
  readonly runtime: string;
  readonly effective_model: string | null;
  readonly tier: string;
  readonly created_at: string;
}

export interface WorkbenchRecentEvent {
  readonly stream: "event" | "decision";
  readonly stream_seq: number;
  readonly entry_id: string;
  readonly kind: string;
  readonly recorded_at: string;
  readonly task_id: string | null;
  readonly episode_id: string | null;
  readonly outcome: string | null;
  readonly reason: string | null;
}

export interface WorkbenchState {
  readonly as_of: JournalBoundary;
  readonly automation_paused: boolean;
  readonly foreground_task_id: string | null;
  readonly next_task_id: string | null;
  readonly tasks: readonly WorkbenchTask[];
  readonly candidates: readonly WorkbenchCandidate[];
  readonly recent_events: readonly WorkbenchRecentEvent[];
}

interface ApiEnvelope<T> {
  readonly success: boolean;
  readonly data: T | null;
  readonly error: string | { readonly message?: string } | null;
}

function commandId(prefix: string): string {
  const suffix =
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `workbench-${prefix}-${suffix}`;
}

function errorMessage(error: ApiEnvelope<unknown>["error"]): string | null {
  if (typeof error === "string") return error;
  if (error && typeof error.message === "string") return error.message;
  return null;
}

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, options);
  let envelope: ApiEnvelope<T> | null = null;
  try {
    envelope = (await response.json()) as ApiEnvelope<T>;
  } catch {
    if (!response.ok) {
      throw new Error(`Model OS 请求失败（${response.status}）`);
    }
  }
  const detail = envelope ? errorMessage(envelope.error) : null;
  if (!response.ok || !envelope?.success || envelope.data === null) {
    throw new Error(detail ?? `Model OS 请求失败（${response.status}）`);
  }
  return envelope.data;
}

const jsonHeaders = { "Content-Type": "application/json" };

export function fetchWorkbench(): Promise<WorkbenchState> {
  return request<WorkbenchState>(`${MODEL_OS_API_BASE}/workbench`);
}

export function setWorkbenchAutomation(
  paused: boolean,
): Promise<WorkbenchState> {
  return request<WorkbenchState>(`${MODEL_OS_API_BASE}/automation`, {
    method: "POST",
    headers: jsonHeaders,
    body: JSON.stringify({
      paused,
      idempotency_key: commandId("automation"),
    }),
  });
}

export function setWorkbenchTaskWarm(
  taskId: string,
  warm: boolean,
): Promise<WorkbenchState> {
  return request<WorkbenchState>(
    `${MODEL_OS_API_BASE}/tasks/${encodeURIComponent(taskId)}/warm`,
    {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify({ warm }),
    },
  );
}

export async function setWorkbenchTaskPriority(
  taskId: string,
  priority: number,
): Promise<void> {
  await request<unknown>(
    `${MODEL_OS_API_BASE}/tasks/${encodeURIComponent(taskId)}/priority`,
    {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify({
        priority,
        idempotency_key: commandId("priority"),
      }),
    },
  );
}

export async function requestWorkbenchForeground(taskId: string): Promise<void> {
  await request<unknown>(
    `${MODEL_OS_API_BASE}/tasks/${encodeURIComponent(taskId)}/foreground`,
    {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify({ idempotency_key: commandId("foreground") }),
    },
  );
}

export async function recordWorkbenchCandidateOutcome(
  sourceKind: WorkbenchCandidate["source_kind"],
  candidateId: string,
  outcome: "adopted" | "dismissed" | "invalid",
  reason?: string,
): Promise<void> {
  await request<unknown>(
    `${MODEL_OS_API_BASE}/${sourceKind}/candidates/${encodeURIComponent(candidateId)}/outcome`,
    {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify({
        command_id: commandId("candidate"),
        outcome,
        ...(reason ? { reason } : {}),
      }),
    },
  );
}

export async function sendWorkbenchInstruction(
  taskId: string,
  text: string,
): Promise<void> {
  await postMessageStream(
    `${MODEL_OS_API_BASE}/workbench/instruction`,
    { task_id: taskId, text },
    () => undefined,
  );
}

export async function replyWorkbenchWaiting(
  taskId: string,
  correlationId: string,
  reply:
    | { readonly answers: Readonly<Record<string, string>> }
    | { readonly decision: string },
): Promise<void> {
  await request<unknown>(
    `${MODEL_OS_API_BASE}/workbench/tasks/${encodeURIComponent(taskId)}/reply`,
    {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify({ correlation_id: correlationId, ...reply }),
    },
  );
}

export function subscribeWorkbench(
  onState: (state: WorkbenchState) => void,
  onError: (message: string) => void,
): () => void {
  const stream = new EventSource(`${MODEL_OS_API_BASE}/workbench/events`);
  stream.onmessage = (event) => {
    try {
      const parsed = JSON.parse(event.data) as
        | WorkbenchState
        | ApiEnvelope<WorkbenchState>;
      if ("success" in parsed) {
        if (parsed.success && parsed.data) {
          onState(parsed.data);
        } else {
          onError(errorMessage(parsed.error) ?? "工作台实时更新失败");
        }
        return;
      }
      onState(parsed);
    } catch {
      onError("工作台收到无法识别的实时更新");
    }
  };
  stream.onerror = () => onError("工作台实时连接已断开，正在重连");
  return () => stream.close();
}
