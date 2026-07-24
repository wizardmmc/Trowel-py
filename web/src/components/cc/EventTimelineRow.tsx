import { useState } from "react";

import { RECOVERABLE_ERROR_SUBCLASSES } from "../../api/ccTypes";
import type {
  CompactBoundaryItem,
  ErrorItem,
  InterruptedItem,
  RetryingItem,
  ThinkingItem,
  TurnItem,
} from "../../stores/ccStore";
import { ApprovalBlock } from "./ApprovalBlock";
import { ElicitationBlock } from "./ElicitationBlock";
import { SubagentBlock } from "./SubagentBlock";
import { ToolBlock } from "./ToolBlock";
import { WorkflowTree } from "./WorkflowTree";

// 这些工具已有专属展示，不能在对话流中重复渲染。
const HIDDEN_TOOLS = new Set([
  "TaskCreate",
  "TaskUpdate",
  "TodoWrite",
  "Workflow",
]);

interface EventTimelineRowProps {
  readonly item: TurnItem;
  readonly onRetryLast?: () => void;
  readonly isReplay?: boolean;
  readonly onAnswer?: (answers: Record<string, string>) => void;
  readonly onCancel?: () => void;
  readonly onApprovalDecision?: (requestId: string, decision: string) => void;
  readonly workdir?: string;
  readonly runtime?: string;
  readonly thinkingComplete?: boolean;
}

export function EventTimelineRow({
  item,
  onRetryLast,
  isReplay,
  onAnswer,
  onCancel,
  onApprovalDecision,
  workdir,
  runtime,
  thinkingComplete,
}: EventTimelineRowProps) {
  switch (item.kind) {
    case "thinking":
      return (
        <ThinkingRow
          item={item}
          runtime={runtime}
          completed={Boolean(thinkingComplete)}
        />
      );
    case "tool": {
      if (HIDDEN_TOOLS.has(item.toolName)) return null;
      if (item.toolName === "Agent") {
        // 历史中可能缺少 task_* 事件，只能用工具终态推断 Agent 状态。
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
    }
    case "subagent":
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
      return <InterruptedRow item={item} runtime={runtime} />;
    case "elicit":
      return (
        <ElicitationBlock
          item={item}
          onAnswer={onAnswer}
          onCancel={onCancel}
          disabled={isReplay}
        />
      );
    case "approval":
      // 短暂断线也会进入 replay，审批是否仍有效由后端注册表裁决。
      return (
        <ApprovalBlock
          item={item}
          onDecision={onApprovalDecision}
        />
      );
    case "workflow":
      return <WorkflowTree workflow={item} workdir={workdir} />;
    case "text":
      return null;
    default:
      return null;
  }
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

function ThinkingRow({
  item,
  runtime,
  completed,
}: {
  readonly item: ThinkingItem;
  readonly runtime?: string;
  readonly completed: boolean;
}) {
  const [open, setOpen] = useState(false);
  const codexVerb = completed ? "Reasoned" : "Reasoning";
  const label =
    runtime === "codex"
      ? item.thinkingDurationSeconds !== undefined
        ? `${codexVerb} for ${item.thinkingDurationSeconds}s`
        : codexVerb
      : item.thinkingDurationSeconds !== undefined
        ? `Thought for ${item.thinkingDurationSeconds}s`
        : "思考";
  return (
    <div className="cc-timeline__row cc-timeline__row--thinking">
      <button
        type="button"
        className="cc-timeline__summary"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
      >
        <span className="cc-timeline__dot" aria-hidden="true">
          ●
        </span>
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
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
      >
        <svg
          className="cc-timeline__icon"
          viewBox="0 0 24 24"
          aria-hidden="true"
        >
          <path d="M3 12a9 9 0 1 0 3-6.7" />
          <path d="M3 4v4h4" />
        </svg>
        <span className="cc-timeline__label">
          {item.attempt > 0 ? `重试 ${item.attempt}${max}` : "重试中"}
          {item.errorStatus !== null && ` (GLM ${item.errorStatus}`}
          {delaySec && `，${delaySec}s 后)`}
          {item.errorStatus !== null && !delaySec && ")"}
        </span>
        <ChevronToggle open={open} label={open ? "收起" : "展开"} />
      </button>
      {open && item.error && (
        <pre className="cc-timeline__detail">{item.error}</pre>
      )}
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

function InterruptedRow({
  item,
  runtime,
}: {
  readonly item: InterruptedItem;
  readonly runtime?: string;
}) {
  void item;
  const host =
    runtime === "codex"
      ? "Codex host"
      : runtime === "claude_code"
        ? "CC 进程"
        : "Agent 进程";
  return (
    <div className="cc-timeline__row cc-timeline__row--interrupted">
      <span className="cc-timeline__label">
        已中断 · {host}已退出，发下一条会自动接上历史
      </span>
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
        <span className="cc-timeline__error-msg">
          出错了，可以重试或换种问法。
        </span>
        <span className="cc-timeline__error-subclass">{item.subclass}</span>
      </div>
      <button
        type="button"
        className="cc-timeline__error-toggle"
        onClick={() => setOpen((value) => !value)}
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
