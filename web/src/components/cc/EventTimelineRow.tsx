/** 按事件种类和 runtime capability 选择一条时间线记录的展示方式。 */

import { memo, useState } from "react";

import { RECOVERABLE_ERROR_SUBCLASSES } from "../../agent/transport";
import type { PerSessionState } from "../../agent/application";
import type {
  CompactBoundaryItem,
  ErrorItem,
  InterruptedItem,
  RetryingItem,
  ThinkingItem,
  TurnItem,
} from "../../agent/domain";
import type {
  AgentCapability,
  RuntimePresentation,
} from "../../agent/runtimes";
import {
  ApprovalBlock,
  ElicitationBlock,
  WorkflowTree,
} from "../../agent/runtimes";
import { SubagentBlock } from "./SubagentBlock";
import { ToolBlock } from "./ToolBlock";

// 这些工具已有专属展示；能力缺失时改为明确提示，不能静默吞掉记录。
const TOOL_PRESENTATION_CAPABILITY: ReadonlyMap<string, AgentCapability> =
  new Map<string, AgentCapability>([
    ["TaskCreate", "tasks"],
    ["TaskUpdate", "tasks"],
    ["TodoWrite", "tasks"],
    ["Workflow", "workflow"],
  ]);

interface EventTimelineRowProps {
  readonly item: TurnItem;
  readonly onRetryLast?: () => void;
  readonly isReplay?: boolean;
  readonly onAnswer?: (answers: Record<string, string>) => void;
  readonly onCancel?: () => void;
  readonly onApprovalDecision?: (requestId: string, decision: string) => void;
  readonly workdir?: string;
  readonly presentation?: RuntimePresentation;
  readonly thinkingComplete?: boolean;
  readonly codexSubagents?: PerSessionState["codexSubagents"];
  readonly onOpenSubagent?: (threadId: string) => void;
  readonly suppressDiffAutoOpen?: boolean;
}

function EventTimelineRowView({
  item,
  onRetryLast,
  isReplay,
  onAnswer,
  onCancel,
  onApprovalDecision,
  workdir,
  presentation,
  thinkingComplete,
  codexSubagents,
  onOpenSubagent,
  suppressDiffAutoOpen,
}: EventTimelineRowProps) {
  switch (item.kind) {
    case "thinking":
      return (
        <ThinkingRow
          item={item}
          presentation={presentation}
          completed={Boolean(thinkingComplete)}
        />
      );
    case "tool": {
      if (!supportsItem(presentation, "tools", isReplay)) {
        return <CapabilityUnavailableRow capability="tools" />;
      }
      const dedicatedCapability = TOOL_PRESENTATION_CAPABILITY.get(item.toolName);
      if (dedicatedCapability !== undefined) {
        return supportsItem(presentation, dedicatedCapability, isReplay) ? (
          null
        ) : (
          <CapabilityUnavailableRow capability={dedicatedCapability} />
        );
      }
      if (item.toolName === "Agent") {
        if (!supportsItem(presentation, "subagents", isReplay)) {
          return <CapabilityUnavailableRow capability="subagents" />;
        }
        // 历史中可能缺少 task_* 事件，只能用工具终态推断 Agent 状态。
        const fallback =
          item.status === "done" || isReplay ? "completed" : "progress";
        return (
          <SubagentBlock
            subagent={item.subagent ?? { status: fallback }}
            childTools={item.childTools}
            workdir={workdir}
            codexSubagents={codexSubagents}
            onOpen={onOpenSubagent}
          />
        );
      }
      return (
        <ToolBlock
          item={item}
          workdir={workdir}
          showCodexMcpPresentation={supportsItem(
            presentation,
            "mcp",
            isReplay,
          )}
          suppressDiffAutoOpen={suppressDiffAutoOpen}
        />
      );
    }
    case "subagent":
      if (!supportsItem(presentation, "subagents", isReplay)) {
        return <CapabilityUnavailableRow capability="subagents" />;
      }
      return (
        <SubagentBlock
          subagent={item.subagent}
          workdir={workdir}
          codexSubagents={codexSubagents}
          onOpen={onOpenSubagent}
        />
      );
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
      return <InterruptedRow item={item} presentation={presentation} />;
    case "elicit":
      if (!supportsItem(presentation, "question", isReplay)) {
        return <CapabilityUnavailableRow capability="question" />;
      }
      return (
        <ElicitationBlock
          item={item}
          onAnswer={onAnswer}
          onCancel={onCancel}
          disabled={Boolean(isReplay) || (!onAnswer && !onCancel)}
        />
      );
    case "approval":
      if (!supportsItem(presentation, "approval", isReplay)) {
        return <CapabilityUnavailableRow capability="approval" />;
      }
      // 短暂断线也会进入 replay，审批是否仍有效由后端注册表裁决。
      return (
        <ApprovalBlock
          item={item}
          onDecision={onApprovalDecision}
          disabled={Boolean(isReplay) || !onApprovalDecision}
        />
      );
    case "workflow":
      if (!supportsItem(presentation, "workflow", isReplay)) {
        return <CapabilityUnavailableRow capability="workflow" />;
      }
      return <WorkflowTree workflow={item} workdir={workdir} />;
    case "text":
      return null;
    default:
      return null;
  }
}

export const EventTimelineRow = memo(EventTimelineRowView);

function supportsItem(
  presentation: RuntimePresentation | undefined,
  capability: AgentCapability,
  isReplay: boolean | undefined,
): boolean {
  return (
    presentation?.supports(
      capability,
      isReplay ? "history" : "live",
    ) ?? true
  );
}

function CapabilityUnavailableRow({
  capability,
}: {
  readonly capability: AgentCapability;
}) {
  return (
    <div className="cc-timeline__row cc-timeline__row--local" role="status">
      当前会话未声明 {capability} 能力，相关记录没有按 runtime 专属样式展示。
    </div>
  );
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
  presentation,
  completed,
}: {
  readonly item: ThinkingItem;
  readonly presentation?: RuntimePresentation;
  readonly completed: boolean;
}) {
  const [open, setOpen] = useState(false);
  const label = presentation
    ? presentation.timelinePresenters.thinkingLabel(
        item.thinkingDurationSeconds,
        completed,
      )
    : item.thinkingDurationSeconds !== undefined
      ? `Thought for ${item.thinkingDurationSeconds}s`
      : completed
        ? "Thought"
        : "Thinking";
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
  presentation,
}: {
  readonly item: InterruptedItem;
  readonly presentation?: RuntimePresentation;
}) {
  void item;
  const host = presentation?.headerStatus.interruptedHostLabel ?? "Agent 进程";
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
