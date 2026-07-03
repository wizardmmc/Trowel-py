import type { SubagentState } from "../../stores/ccStore";

interface SubagentBlockProps {
  readonly subagent: SubagentState;
}

/** Read total_tokens off the usage dict (number | undefined). */
function tokenCount(usage: SubagentState["usage"]): number | null {
  if (!usage) return null;
  const t = (usage as Record<string, unknown>).total_tokens;
  return typeof t === "number" ? t : null;
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

/** Completion summary, e.g. " (15 tool uses · 64.8k tokens · 5m 8s)". Only
 * fields actually present (>0) are shown — GLM backend reports total_tokens=0
 * for sub-agents, so tokens are omitted when zero rather than showing "0 tokens". */
function formatUsage(usage: SubagentState["usage"]): string {
  if (!usage) return "";
  const u = usage as Record<string, unknown>;
  const toolUses = typeof u.tool_uses === "number" ? u.tool_uses : null;
  const tokens = typeof u.total_tokens === "number" ? u.total_tokens : null;
  const durationMs = typeof u.duration_ms === "number" ? u.duration_ms : null;
  const parts: string[] = [];
  if (toolUses !== null && toolUses > 0) parts.push(`${toolUses} tool uses`);
  if (tokens !== null && tokens > 0) parts.push(`${formatTokens(tokens)} tokens`);
  if (durationMs !== null && durationMs > 0) parts.push(formatDuration(durationMs));
  return parts.length ? ` (${parts.join(" · ")})` : "";
}

function brief(text: string, max = 40): string {
  const oneLine = text.replace(/\s+/g, " ").trim();
  return oneLine.length > max ? oneLine.slice(0, max - 1) + "…" : oneLine;
}

/**
 * Sub-agent (Agent tool) inline block — soil-brown edge, shows description /
 * type / last tool / token spend / progress (slice-025-a A3).
 *
 * Used both for an Agent ToolItem with sub-agent progress attached AND for the
 * standalone degradation row (when no Agent tool matched the tool_use_id). The
 * 'in progress' state uses a rotating ring (cc-style; design.md §1).
 */
export function SubagentBlock({ subagent }: SubagentBlockProps) {
  const inProgress = subagent.status !== "completed";
  const tokens = tokenCount(subagent.usage);

  return (
    <div className="cc-subagent" data-status={subagent.status}>
      <svg className="cc-subagent__icon" viewBox="0 0 24 24" aria-hidden="true">
        <rect x="4" y="4" width="16" height="16" rx="2" />
        <path d="M9 9h6M9 13h6M9 17h4" />
      </svg>
      <span className="cc-subagent__name">
        Agent{subagent.subagent_type ? ` · ${subagent.subagent_type}` : ""}
      </span>
      {subagent.description && (
        <span className="cc-subagent__desc">{brief(subagent.description)}</span>
      )}
      {subagent.last_tool_name && (
        <span className="cc-subagent__last">last: {subagent.last_tool_name}</span>
      )}
      {tokens !== null && (
        <span className="cc-subagent__tokens">{formatTokens(tokens)} tok</span>
      )}
      {inProgress ? (
        <span className="cc-subagent__spin cc-spin-ring" aria-label="进行中" />
      ) : (
        <span className="cc-subagent__done">Done{formatUsage(subagent.usage)}</span>
      )}
    </div>
  );
}
