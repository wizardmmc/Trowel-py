import type { WorkflowItem } from "../../stores/ccReducer";

export function formatWorkflowTokens(tokens: number): string {
  return tokens >= 1000 ? `${(tokens / 1000).toFixed(1)}k` : String(tokens);
}

function formatDuration(durationMs: number): string {
  const seconds = Math.round(durationMs / 1000);
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const remainingSeconds = seconds % 60;
  return remainingSeconds ? `${minutes}m ${remainingSeconds}s` : `${minutes}m`;
}

export function briefWorkflowText(text: string, max = 48): string {
  const oneLine = text.replace(/\s+/g, " ").trim();
  return oneLine.length > max ? `${oneLine.slice(0, max - 1)}…` : oneLine;
}

export function WorkflowCaret({ open }: { readonly open: boolean }) {
  return (
    <svg
      className="cc-wf-caret"
      viewBox="0 0 24 24"
      aria-hidden="true"
      style={{ transform: open ? undefined : "rotate(-90deg)" }}
    >
      <path d="M6 9l6 6 6-6" fill="none" stroke="currentColor" strokeWidth="2" />
    </svg>
  );
}

const STATUS_LABEL: Record<WorkflowItem["status"], string> = {
  running: "running",
  completed: "✓ completed",
  killed: "⊘ killed",
  failed: "✗ failed",
};

function StatusBadge({ status }: { readonly status: WorkflowItem["status"] }) {
  return (
    <span className={`cc-wf-status cc-wf-status--${status}`}>
      {status === "running" && <span className="cc-spin-ring" aria-hidden="true" />}
      {STATUS_LABEL[status]}
    </span>
  );
}

function Progress({
  done,
  total,
  status,
}: {
  readonly done: number;
  readonly total: number;
  readonly status: WorkflowItem["status"];
}) {
  if (total <= 0) return null;
  const percentage = Math.round((done / total) * 100);
  const fillClass = status === "running" ? "cc-wf-progress__fill--running" : "";
  return (
    <span className="cc-wf-progress">
      <span className="cc-wf-progress__bar">
        <span
          className={`cc-wf-progress__fill ${fillClass}`}
          style={{ width: `${percentage}%` }}
        />
      </span>
      {done}/{total}
    </span>
  );
}

function Stats({
  tokens,
  tools,
  durationMs,
}: {
  readonly tokens: number | null;
  readonly tools: number | null;
  readonly durationMs: number | null;
}) {
  return (
    <span className="cc-wf-stats">
      {tokens !== null && tokens > 0 && (
        <span>
          <b>{formatWorkflowTokens(tokens)}</b> tok
        </span>
      )}
      {tools !== null && tools > 0 && (
        <span>
          <b>{tools}</b> tools
        </span>
      )}
      {durationMs !== null && durationMs > 0 && (
        <span>
          <b>{formatDuration(durationMs)}</b>
        </span>
      )}
    </span>
  );
}

export function WorkflowSummary({ workflow }: { readonly workflow: WorkflowItem }) {
  return (
    <>
      <svg className="cc-workflow__icon" viewBox="0 0 24 24" aria-hidden="true">
        <rect x="3" y="3" width="7" height="7" rx="1" />
        <rect x="14" y="3" width="7" height="7" rx="1" />
        <rect x="3" y="14" width="7" height="7" rx="1" />
        <rect x="14" y="14" width="7" height="7" rx="1" />
        <path d="M10 6.5h4M6.5 10v4M17.5 10v4M10 17.5h4" />
      </svg>
      <span className="cc-workflow__name">Workflow · {workflow.name}</span>
      {workflow.args && (
        <span className="cc-workflow__args">{briefWorkflowText(workflow.args)}</span>
      )}
      <StatusBadge status={workflow.status} />
      <Progress
        done={workflow.doneCount}
        total={workflow.agentCount}
        status={workflow.status}
      />
      <Stats
        tokens={workflow.totalTokens}
        tools={workflow.totalToolCalls}
        durationMs={workflow.durationMs}
      />
    </>
  );
}
