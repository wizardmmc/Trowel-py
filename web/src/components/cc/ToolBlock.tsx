import { useState } from "react";

import type { ToolItem } from "../../stores/ccStore";

/**
 * One tool call rendered as a summary line + collapsible detail.
 *
 * Write/Bash get a semantic layout (path+preview / command+output); every
 * other tool falls back to a collapsible JSON tree of input + result. This
 * matches the spec's "v1 混合" rule: cover the two common tools well, JSON-tree
 * the long tail instead of chasing CC's tool catalog.
 *
 * The summary line is the "terminal-style" affordance: `⚙ <tool> <摘要> ✓ <耗时>`
 * at a glance, detail on click.
 */
interface ToolBlockProps {
  readonly item: ToolItem;
}

/** Truncate to keep the summary line a single line. */
function brief(text: string, max = 48): string {
  const oneLine = text.replace(/\s+/g, " ").trim();
  return oneLine.length > max ? oneLine.slice(0, max - 1) + "…" : oneLine;
}

function WriteSummary({ input }: { readonly input: Record<string, unknown> }) {
  const path = typeof input.file_path === "string" ? input.file_path : "";
  return <span className="cc-tool__brief">{brief(path, 60)}</span>;
}

function BashSummary({ input }: { readonly input: Record<string, unknown> }) {
  const cmd = typeof input.command === "string" ? input.command : "";
  return <code className="cc-tool__brief cc-tool__brief--mono">{brief(cmd, 60)}</code>;
}

function JsonTree({ label, data }: { readonly label: string; readonly data: unknown }) {
  return (
    <div className="cc-tool__json">
      <span className="cc-tool__json-label">{label}</span>
      <pre className="cc-tool__json-body">{JSON.stringify(data, null, 2)}</pre>
    </div>
  );
}

export function ToolBlock({ item }: ToolBlockProps) {
  const [open, setOpen] = useState(false);
  const done = item.status === "done";
  const seconds =
    item.elapsedSeconds !== null ? `${item.elapsedSeconds.toFixed(1)}s` : null;

  let summary: React.ReactNode;
  if (item.toolName === "Write" || item.toolName === "Edit" || item.toolName === "MultiEdit") {
    summary = <WriteSummary input={item.input} />;
  } else if (item.toolName === "Bash") {
    summary = <BashSummary input={item.input} />;
  } else {
    summary = <span className="cc-tool__brief">{brief(item.toolName)}</span>;
  }

  return (
    <div className="cc-tool" data-status={item.status}>
      <button
        type="button"
        className="cc-tool__summary"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
      >
        <svg className="cc-tool__icon" viewBox="0 0 24 24" aria-hidden="true">
          {/* gear — line icon, no emoji */}
          <circle cx="12" cy="12" r="3" />
          <path d="M12 2v3M12 19v3M2 12h3M19 12h3M5 5l2 2M17 17l2 2M19 5l-2 2M7 17l-2 2" />
        </svg>
        <span className="cc-tool__name">{item.toolName}</span>
        {summary}
        {done && (
          <span className="cc-tool__check" aria-label="完成">
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M5 13l4 4L19 7" />
            </svg>
            {seconds && <span className="cc-tool__elapsed">{seconds}</span>}
          </span>
        )}
        {!done && seconds && (
          <span className="cc-tool__elapsed cc-tool__elapsed--running">{seconds}</span>
        )}
      </button>
      {open && (
        <div className="cc-tool__detail">
          {item.toolName === "Bash" ? (
            <>
              <pre className="cc-tool__bash-cmd">
                {typeof item.input.command === "string" ? item.input.command : ""}
              </pre>
              {item.result !== null && (
                <pre className="cc-tool__bash-out">{item.result}</pre>
              )}
            </>
          ) : item.toolName === "Write" || item.toolName === "Edit" || item.toolName === "MultiEdit" ? (
            <>
              <div className="cc-tool__path">
                {typeof item.input.file_path === "string" ? item.input.file_path : ""}
              </div>
              {item.result !== null && (
                <pre className="cc-tool__preview">{brief(item.result, 240)}</pre>
              )}
            </>
          ) : (
            <>
              <JsonTree label="input" data={item.input} />
              {item.result !== null && <JsonTree label="result" data={item.result} />}
            </>
          )}
        </div>
      )}
    </div>
  );
}
