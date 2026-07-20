import { useState } from "react";
import type { SubagentState, ToolItem } from "../../stores/ccStore";
import { ToolBlock } from "./ToolBlock";

/** Max child rows shown while a sub-agent is still running (slice-025-a 阶段B). */
const CC_VISIBLE_RUNNING_TOOLS = 4;
/** Child rows shown once the sub-agent completes (latest one; older collapse). */
const CC_VISIBLE_DONE_TOOLS = 1;

interface SubagentBlockProps {
  readonly subagent: SubagentState;
  /** Internal tool_uses the sub-agent spawned (envelope parent_tool_use_id
   * pointed at this Agent). Absent for the standalone degradation row. */
  readonly childTools?: readonly ToolItem[];
  /** slice-029: forwarded to child ToolBlocks for project-relative path display. */
  readonly workdir?: string;
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
 * type / last tool / token spend / progress (slice-025-a A3), plus a collapsible
 * list of the sub-agent's internal tool_uses indented underneath (阶段B).
 *
 * Each child renders via `<ToolBlock>` — the same summary line a top-level tool
 * gets (gear + name + input brief, e.g. "Bash printf … > /path") — so a child
 * call reads exactly like a normal tool call, just nested under the brown edge.
 *
 * Children region: shows the latest N ToolBlocks (N=4 running / 1 completed);
 * older scroll off the top. Click '+N more' or the header to expand all.
 *
 * Note: history replay cannot populate childTools — cc's persisted jsonl drops
 * the parent_tool_use_id envelope field (live stream only).
 */
export function SubagentBlock({ subagent, childTools, workdir }: SubagentBlockProps) {
  const [expanded, setExpanded] = useState(false);
  // slice-077-prefix: a subagent is in-progress ONLY on started/progress.
  // Terminal statuses (completed/failed/cancelled/unknown) all stop the
  // spinner — 失败测试 6: failed/cancelled 不留永久 spinner.
  const inProgress =
    subagent.status === "started" || subagent.status === "progress";
  const tokens = tokenCount(subagent.usage);

  const kids = childTools ?? [];
  const hasKids = kids.length > 0;
  const autoCount = inProgress
    ? CC_VISIBLE_RUNNING_TOOLS
    : CC_VISIBLE_DONE_TOOLS;
  const visibleCount = expanded ? kids.length : Math.min(autoCount, kids.length);
  const hiddenCount = kids.length - visibleCount;
  // Latest N: slice from the tail (new tools append at the end; the oldest
  // scroll off the top — "bottom-up scroll" per spec).
  const visibleChildren = kids.slice(-visibleCount);
  const toggle = (): void => setExpanded((e) => !e);

  return (
    <div className="cc-subagent" data-status={subagent.status}>
      <div
        className="cc-subagent__header"
        role={hasKids ? "button" : undefined}
        tabIndex={hasKids ? 0 : undefined}
        aria-expanded={hasKids ? expanded : undefined}
        onClick={hasKids ? toggle : undefined}
        onKeyDown={
          hasKids
            ? (e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  toggle();
                }
              }
            : undefined
        }
      >
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
          <span className="cc-subagent__done">
            Done{formatUsage(subagent.usage)}
          </span>
        )}
      </div>
      {hasKids && (
        <div className="cc-subagent__children">
          {visibleChildren.map((c) => (
            // slice-029: sub-agent children render condensed (summary stat
            // only, no expandable diff) — a chain of sub-agent Edit diffs
            // would drown the timeline. Matches CC `style:'condensed'`.
            <ToolBlock key={c.toolUseId} item={c} condensed workdir={workdir} />
          ))}
          {hiddenCount > 0 && (
            <button
              type="button"
              className="cc-subagent__more"
              onClick={() => setExpanded(true)}
            >
              +{hiddenCount} more
            </button>
          )}
        </div>
      )}
    </div>
  );
}
