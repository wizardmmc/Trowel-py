import { useState } from "react";

import type { WorkflowItem } from "../../stores/ccReducer";
import type { WorkflowAgentInfo, WorkflowPhaseInfo } from "../../api/ccTypes";

/**
 * Workflow progress tree (slice-036).
 *
 * cc runs Workflows in the background and pushes nothing about them to its
 * stream-json stdout; the BE reads the on-disk wf_<runId>.json and emits a
 * WorkflowTreeEvent, which the reducer stores as a WorkflowItem. This renders
 * it as a three-level collapsible tree:
 *
 *   workflow root card (soil-brown thick edge)
 *   └─ phase group (gold rule, NO numeric badge — mockup decision)
 *      └─ agent node (soil-brown thin edge, reuses SubagentBlock's language)
 *
 * Rendered identically for the live path (running → completed) and history
 * replay (invariant C-1): same component, same data shape, same detail level
 * (phase folds / agent expands / prompt+result previews). The only live-only
 * affordance is the running pulse on in-flight agent dots.
 *
 * Tool transparency (P3) — per-agent tool_use list — is not yet wired; the
 * agent body currently shows prompt/result previews only.
 */
interface WorkflowTreeProps {
  readonly workflow: WorkflowItem;
  /** Forwarded to future child ToolBlocks for project-relative paths. Unused
   * today (P3 tool transparency will need it). */
  readonly workdir?: string;
}

function formatTokens(n: number): string {
  return n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n);
}

function formatDuration(ms: number): string {
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rs = s % 60;
  return rs ? `${m}m ${rs}s` : `${m}m`;
}

function brief(text: string, max = 48): string {
  const oneLine = text.replace(/\s+/g, " ").trim();
  return oneLine.length > max ? oneLine.slice(0, max - 1) + "…" : oneLine;
}

/** Collapsed/expanded caret (matches the mockup's cc-wf-caret SVG). */
function Caret({ open }: { readonly open: boolean }) {
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
  const pct = Math.round((done / total) * 100);
  const fillClass = status === "running" ? "cc-wf-progress__fill--running" : "";
  return (
    <span className="cc-wf-progress">
      <span className="cc-wf-progress__bar">
        <span
          className={`cc-wf-progress__fill ${fillClass}`}
          style={{ width: `${pct}%` }}
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
          <b>{formatTokens(tokens)}</b> tok
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

/** One agent node — soil-brown thin edge, state dot, label/model/tokens.
 * Clickable to expand prompt/result previews when present. */
function AgentNode({
  agent,
  defaultOpen,
}: {
  readonly agent: WorkflowAgentInfo;
  readonly defaultOpen: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const hasPreview = Boolean(agent.prompt_preview || agent.result_preview);
  const tokens = agent.tokens ?? 0;
  const toolCalls = agent.tool_calls ?? 0;
  return (
    <div className="cc-agent" data-state={agent.state}>
      <div
        className="cc-agent__head"
        role={hasPreview ? "button" : undefined}
        tabIndex={hasPreview ? 0 : undefined}
        aria-expanded={hasPreview ? open : undefined}
        onClick={hasPreview ? () => setOpen((o) => !o) : undefined}
        onKeyDown={
          hasPreview
            ? (e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  setOpen((o) => !o);
                }
              }
            : undefined
        }
      >
        {hasPreview && <Caret open={open} />}
        <span className="cc-agent__dot" aria-hidden="true" />
        <span className="cc-agent__label">{agent.label}</span>
        {agent.model && <span className="cc-agent__model">{agent.model}</span>}
        {agent.last_tool_name && (
          <span className="cc-agent__last">last: {agent.last_tool_name}</span>
        )}
        {toolCalls > 0 && <span className="cc-agent__tools">{toolCalls} tools</span>}
        {tokens > 0 && (
          <span className="cc-agent__tok">{formatTokens(tokens)} tok</span>
        )}
      </div>
      {open && hasPreview && (
        <div className="cc-agent__body">
          {agent.prompt_preview && (
            <div className="cc-agent__preview">
              <b>prompt</b>
              {brief(agent.prompt_preview, 200)}
            </div>
          )}
          {agent.result_preview && (
            <div className="cc-agent__preview">
              <b>result</b>
              {brief(agent.result_preview, 200)}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/** One phase group — gold rule, title/detail, agent count, collapsible. */
function PhaseRow({
  phase,
  agents,
}: {
  readonly phase: WorkflowPhaseInfo;
  readonly agents: readonly WorkflowAgentInfo[];
}) {
  const [open, setOpen] = useState(true);
  const done = agents.filter((a) => a.state === "done").length;
  const total = agents.length;
  const running = agents.some((a) => a.state === "running");
  return (
    <div className="cc-phase" aria-expanded={open}>
      <div
        className="cc-phase__head"
        role="button"
        tabIndex={0}
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            setOpen((o) => !o);
          }
        }}
      >
        <Caret open={open} />
        <span className="cc-phase__title">{phase.title}</span>
        {phase.detail && <span className="cc-phase__detail">{phase.detail}</span>}
        <span className="cc-phase__count">
          {total > 0
            ? running
              ? `${done}/${total} running`
              : `${done}/${total} done`
            : "pending"}
        </span>
      </div>
      {open && total > 0 && (
        <div className="cc-phase__agents">
          {agents.map((a, i) => (
            <AgentNode key={a.agent_id || i} agent={a} defaultOpen={false} />
          ))}
        </div>
      )}
    </div>
  );
}

export function WorkflowTree({ workflow }: WorkflowTreeProps) {
  const [open, setOpen] = useState(true);
  // Group agents by phase_title; an agent whose phase_title matches no phase
  // lands in an "(other)" bucket so it isn't lost (defensive — wf.json
  // normally has every agent under a declared phase).
  const grouped = new Map<string, WorkflowAgentInfo[]>();
  for (const ph of workflow.phases) grouped.set(ph.title, []);
  const other: WorkflowAgentInfo[] = [];
  for (const a of workflow.agents) {
    const bucket = a.phase_title && grouped.has(a.phase_title) ? grouped.get(a.phase_title) : null;
    if (bucket) bucket.push(a);
    else other.push(a);
  }
  return (
    <div className="cc-workflow" data-status={workflow.status} aria-expanded={open}>
      <div
        className="cc-workflow__header"
        role="button"
        tabIndex={0}
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            setOpen((o) => !o);
          }
        }}
      >
        <Caret open={open} />
        <svg className="cc-workflow__icon" viewBox="0 0 24 24" aria-hidden="true">
          <rect x="3" y="3" width="7" height="7" rx="1" />
          <rect x="14" y="3" width="7" height="7" rx="1" />
          <rect x="3" y="14" width="7" height="7" rx="1" />
          <rect x="14" y="14" width="7" height="7" rx="1" />
          <path d="M10 6.5h4M6.5 10v4M17.5 10v4M10 17.5h4" />
        </svg>
        <span className="cc-workflow__name">Workflow · {workflow.name}</span>
        {workflow.args && (
          <span className="cc-workflow__args">{brief(workflow.args)}</span>
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
      </div>
      {open && (
        <div className="cc-workflow__body">
          {workflow.error && (
            <div className="cc-wf-error">
              <div className="cc-wf-error__label">
                {workflow.status === "killed" ? "Workflow aborted" : "Workflow failed"}
              </div>
              {brief(workflow.error, 240)}
            </div>
          )}
          {workflow.phases.map((ph) => (
            <PhaseRow
              key={ph.title}
              phase={ph}
              agents={grouped.get(ph.title) ?? []}
            />
          ))}
          {other.length > 0 && (
            <PhaseRow
              phase={{ title: "(other)", detail: null }}
              agents={other}
            />
          )}
        </div>
      )}
    </div>
  );
}
