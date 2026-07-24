import { useState } from "react";
import type { SubagentState, ToolItem } from "../../stores/ccStore";
import { ToolBlock } from "./ToolBlock";

const CC_VISIBLE_RUNNING_TOOLS = 4;
const CC_VISIBLE_DONE_TOOLS = 1;

interface SubagentBlockProps {
  readonly subagent: SubagentState;
  readonly childTools?: readonly ToolItem[];
  readonly workdir?: string;
}

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

export function SubagentBlock({ subagent, childTools, workdir }: SubagentBlockProps) {
  const [expanded, setExpanded] = useState(false);
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
