import { Fragment, type ReactNode, useState } from "react";

import type {
  InterruptedItem,
  ErrorItem,
  RetryingItem,
  ThinkingItem,
  CompactBoundaryItem,
  TurnItem,
} from "../../stores/ccStore";
import { RECOVERABLE_ERROR_SUBCLASSES } from "../../api/ccTypes";
import { AssistantText } from "./AssistantText";
import { SubagentBlock } from "./SubagentBlock";
import { ToolBlock } from "./ToolBlock";
import { ElicitationBlock } from "./ElicitationBlock";

/**
 * The turn body renderer. Walks `turn.items` in true order and interleaves:
 * consecutive text items merge into one AssistantText markdown block (a DOM
 * optimization — order is preserved), and process items (thinking / tool /
 * retrying / stalled / compact_boundary / error / interrupted / …) render as
 * bare rows at the position they actually occurred.
 *
 * slice-025-b B1: previously this component filtered text out and rendered only
 * a process-item sequence inside a `.cc-timeline` gutter; MessageList bucketed
 * all text in front. That hid the real order. Now text + process share one
 * in-order pass and the gutter is gone (terminal cc style: process rows sit
 * between text blocks, no global timeline rail).
 *
 * error + interrupted render here as the turn-tail block (red block / sunshine
 * soft-transition badge), with a conditional retry button on recoverable errors.
 */
interface EventTimelineProps {
  readonly items: readonly TurnItem[];
  readonly onRetryLast?: () => void;
  /** True when rendering a finalized (non-active) turn from history. Used so an
   * Agent that never got a tool_result (e.g. stalled mid-Agent) doesn't spin
   * forever in the replay view. */
  readonly isReplay?: boolean;
  /** Submit answers for a pending AskUserQuestion (slice-025-c). */
  readonly onAnswer?: (answers: Record<string, string>) => void;
  /** Decline a pending AskUserQuestion. */
  readonly onCancel?: () => void;
  /** slice-029: the session's cwd, so Edit/Write paths render project-relative
   * (CC `getDisplayPath`) instead of full absolute. Optional — omit for tests. */
  readonly workdir?: string;
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
 * Render one process item. Text items are NOT rendered here — EventTimeline's
 * main loop merges consecutive text into AssistantText blocks before reaching
 * Row. The text case stays as a defensive null.
 */
function Row({
  item,
  onRetryLast,
  isReplay,
  onAnswer,
  onCancel,
  workdir,
}: {
  readonly item: TurnItem;
  readonly onRetryLast?: () => void;
  readonly isReplay?: boolean;
  readonly onAnswer?: (answers: Record<string, string>) => void;
  readonly onCancel?: () => void;
  readonly workdir?: string;
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
        const fallback =
          item.status === "done" || isReplay ? "completed" : "progress";
        return (
          <SubagentBlock
            subagent={item.subagent ?? { status: fallback }}
            childTools={item.childTools}
            workdir={workdir}
          />
        );
      }
      return <ToolBlock item={item} workdir={workdir} />;
    case "subagent":
      // Standalone degradation row (no matching Agent ToolItem — decision #10).
      return <SubagentBlock subagent={item.subagent} workdir={workdir} />;
    case "retrying":
      return <RetryingRow item={item} />;
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
    case "elicit":
      return (
        <ElicitationBlock
          item={item}
          onAnswer={onAnswer}
          onCancel={onCancel}
          disabled={isReplay}
        />
      );
    case "text":
      // Handled by EventTimeline's main loop (merged into AssistantText).
      return null;
    default:
      return null;
  }
}

export function EventTimeline({
  items,
  onRetryLast,
  isReplay,
  onAnswer,
  onCancel,
  workdir,
}: EventTimelineProps) {
  // One in-order pass: consecutive text items merge into a single AssistantText
  // markdown block; every other item renders via Row at its real position.
  //
  // Returns a Fragment (not a wrapper <div>): AssistantText and each Row become
  // DIRECT children of .cc-msg__body, which is what lets the
  // `.cc-msg--assistant .cc-msg__body > * + *` selector space them. Wrapping
  // these blocks in a container div would break that rhythm — keep this透明.
  const blocks: ReactNode[] = [];
  let textBuf = "";
  let key = 0;
  const flushText = () => {
    if (textBuf !== "") {
      blocks.push(<AssistantText key={`t${key++}`} text={textBuf} />);
      textBuf = "";
    }
  };
  for (const item of items) {
    if (item.kind === "text") {
      // Join consecutive text items with a blank line. ccStore's text case
      // already folds a same-run delta into one TextItem, so this branch
      // normally runs once per text run. But if items ever carry two adjacent
      // TextItems (two assistant envelopes with no tool between, or a future
      // reducer change), a bare concat would collapse them into one paragraph.
      // The blank-line join keeps the paragraph boundary → markdown renders two
      // <p>. Zero effect on the normal single-item path.
      textBuf = textBuf ? `${textBuf}\n\n${item.text}` : item.text;
    } else {
      flushText();
      blocks.push(
        <Row
          key={`p${key++}`}
          item={item}
          onRetryLast={onRetryLast}
          isReplay={isReplay}
          onAnswer={onAnswer}
          onCancel={onCancel}
          workdir={workdir}
        />,
      );
    }
  }
  flushText();
  if (blocks.length === 0) return null;
  return <Fragment>{blocks}</Fragment>;
}
