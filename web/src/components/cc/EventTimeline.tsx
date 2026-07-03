import { useState } from "react";

import type {
  InterruptedItem,
  ErrorItem,
  RetryingItem,
  ThinkingItem,
  CompactBoundaryItem,
  TurnItem,
} from "../../stores/ccStore";
import { RECOVERABLE_ERROR_SUBCLASSES } from "../../api/ccTypes";
import { SubagentBlock } from "./SubagentBlock";
import { ToolBlock } from "./ToolBlock";

/**
 * The "bare-row" process timeline: thinking / tool / retrying / stalled /
 * compact_boundary items render as inline summary lines between the dialogue
 * cards. Each shows one line at a glance, with the raw detail folded away —
 * the "see what happened without being shouted at" affordance.
 *
 * error + interrupted render here too as the turn-tail block (red block /
 * sunshine soft-transition badge), with a conditional retry button on
 * recoverable errors.
 */
interface EventTimelineProps {
  readonly items: readonly TurnItem[];
  readonly onRetryLast?: () => void;
  /** True when rendering a finalized (non-active) turn from history. Used so an
   * Agent that never got a tool_result (e.g. stalled mid-Agent) doesn't spin
   * forever in the replay view. */
  readonly isReplay?: boolean;
}

function ChevronToggle({
  open,
  label,
}: {
  readonly open: boolean;
  readonly label: string;
}) {
  return (
    <span className="cc-timeline__chevron" aria-label={label}>
      {open ? "▾" : "▸"}
    </span>
  );
}

function ThinkingRow({ item }: { readonly item: ThinkingItem }) {
  const [open, setOpen] = useState(false);
  // slice-025-a A2: show "Thought for Ns" when a heartbeat-measured duration is
  // stamped on the item; fall back to a bare "思考" when no heartbeat preceded
  // (e.g. non-GLM backend or history replay).
  const label =
    item.thinkingDurationSeconds !== undefined
      ? `Thought for ${item.thinkingDurationSeconds}s`
      : "思考";
  return (
    <div className="cc-timeline__row cc-timeline__row--thinking">
      <button
        type="button"
        className="cc-timeline__summary"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
      >
        <span className="cc-timeline__dot" aria-hidden="true">●</span>
        <span className="cc-timeline__label">{label}</span>
        <ChevronToggle open={open} label={open ? "收起" : "展开"} />
      </button>
      {open && <pre className="cc-timeline__detail">{item.text}</pre>}
    </div>
  );
}

function RetryingRow({ item }: { readonly item: RetryingItem }) {
  const [open, setOpen] = useState(false);
  const delaySec =
    item.retryDelayMs !== null ? (item.retryDelayMs / 1000).toFixed(0) : null;
  const max = item.maxRetries !== null ? `/${item.maxRetries}` : "";
  return (
    <div className="cc-timeline__row cc-timeline__row--retrying">
      <button
        type="button"
        className="cc-timeline__summary"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
      >
        <svg className="cc-timeline__icon" viewBox="0 0 24 24" aria-hidden="true">
          <path d="M3 12a9 9 0 1 0 3-6.7" />
          <path d="M3 4v4h4" />
        </svg>
        <span className="cc-timeline__label">
          重试 {item.attempt}{max}
          {item.errorStatus !== null && ` (GLM ${item.errorStatus}`}
          {delaySec && `，${delaySec}s 后)`}
          {item.errorStatus !== null && !delaySec && ")"}
        </span>
        <ChevronToggle open={open} label={open ? "收起" : "展开"} />
      </button>
      {open && item.error && <pre className="cc-timeline__detail">{item.error}</pre>}
    </div>
  );
}

function StalledRow() {
  return (
    <div className="cc-timeline__row cc-timeline__row--stalled">
      <svg className="cc-timeline__icon" viewBox="0 0 24 24" aria-hidden="true">
        <path d="M6 2h12M6 22h12M9 2v4l-3 6a4 4 0 0 0 4 6h4a4 4 0 0 0 4-6l-3-6V2" />
      </svg>
      <span className="cc-timeline__label">CC 静默，正在自动恢复…</span>
    </div>
  );
}

function CompactRow({ item }: { readonly item: CompactBoundaryItem }) {
  void item;
  return (
    <div className="cc-timeline__divider" role="separator">
      <span className="cc-timeline__divider-line" aria-hidden="true" />
      <span className="cc-timeline__divider-label">自动压缩完成</span>
      <span className="cc-timeline__divider-line" aria-hidden="true" />
    </div>
  );
}

function InterruptedRow({ item }: { readonly item: InterruptedItem }) {
  void item;
  return (
    <div className="cc-timeline__row cc-timeline__row--interrupted">
      <span className="cc-timeline__label">已中断 · CC 进程已退出，发下一条会自动接上历史</span>
    </div>
  );
}

function ErrorRow({
  item,
  onRetryLast,
}: {
  readonly item: ErrorItem;
  readonly onRetryLast?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const recoverable = RECOVERABLE_ERROR_SUBCLASSES.has(item.subclass);
  return (
    <div className="cc-timeline__row cc-timeline__row--error">
      <div className="cc-timeline__error-head">
        <span className="cc-timeline__error-msg">出错了，可以重试或换种问法。</span>
        <span className="cc-timeline__error-subclass">{item.subclass}</span>
      </div>
      <button
        type="button"
        className="cc-timeline__error-toggle"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
      >
        <ChevronToggle open={open} label={open ? "收起详情" : "展开详情"} />
        <span>{open ? "收起详情" : "展开详情"}</span>
      </button>
      {open && item.errors.length > 0 && (
        <pre className="cc-timeline__detail">{item.errors.join("\n")}</pre>
      )}
      {recoverable && onRetryLast && (
        <button
          type="button"
          className="cc-timeline__retry-btn"
          onClick={onRetryLast}
        >
          重试上一条
        </button>
      )}
    </div>
  );
}

/**
 * Render one process item. Text items are NOT rendered here — MessageList
 * renders assistant text as dialogue cards; this component is only the
 * "bare-row" process events + turn-tail error/interrupted.
 */
function Row({
  item,
  onRetryLast,
  isReplay,
}: {
  readonly item: TurnItem;
  readonly onRetryLast?: () => void;
  readonly isReplay?: boolean;
}) {
  switch (item.kind) {
    case "thinking":
      return <ThinkingRow item={item} />;
    case "tool":
      // Agent tool gets the dedicated sub-agent block. When no task_* progress
      // has arrived (e.g. history replay — the cc interactive jsonl carries no
      // task_* events), infer status from the ToolItem itself: a done tool_result
      // means the Agent call finished -> completed; otherwise in-progress.
      // The Agent's input (prompt) / result are NOT expanded here (mockup
      // single-row design; a future slice can add a collapsible detail).
      if (item.toolName === "Agent") {
        // history replay: a turn that already ended (done/error/interrupted)
        // shouldn't show a spinning Agent even if tool_result never arrived
        // (e.g. stalled mid-Agent) — treat as completed in that case.
        const fallback =
          item.status === "done" || isReplay ? "completed" : "progress";
        return <SubagentBlock subagent={item.subagent ?? { status: fallback }} />;
      }
      return <ToolBlock item={item} />;
    case "subagent":
      // Standalone degradation row (no matching Agent ToolItem — decision #10).
      return <SubagentBlock subagent={item.subagent} />;
    case "retrying":
      return <RetryingRow item={item} />;
    case "stalled":
      return <StalledRow />;
    case "compact_boundary":
      return <CompactRow item={item} />;
    case "local_command":
      return (
        <div className="cc-timeline__row cc-timeline__row--local">
          <pre className="cc-timeline__local-cmd">{item.content}</pre>
        </div>
      );
    case "error":
      return <ErrorRow item={item} onRetryLast={onRetryLast} />;
    case "interrupted":
      return <InterruptedRow item={item} />;
    case "text":
      return null; // handled by MessageList
    default:
      return null;
  }
}

export function EventTimeline({ items, onRetryLast, isReplay }: EventTimelineProps) {
  // Render only process items (skip text — that's the dialogue card's job).
  const processItems = items.filter((i) => i.kind !== "text");
  if (processItems.length === 0) return null;
  return (
    <div className="cc-timeline" role="list">
      {processItems.map((item, idx) => (
        <div className="cc-timeline__entry" role="listitem" key={idx}>
          <Row item={item} onRetryLast={onRetryLast} isReplay={isReplay} />
        </div>
      ))}
    </div>
  );
}
