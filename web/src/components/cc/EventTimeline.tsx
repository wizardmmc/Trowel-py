/** 展示单个 turn 的事件序列，并限制超大 turn 的初始挂载量。 */

import { Fragment, useEffect, useState, type ReactNode } from "react";

import type { PerSessionState } from "../../agent/application";
import type { ToolItem, TurnItem } from "../../agent/domain";
import {
  CodexExplorationGroup,
  isCodexExploration,
  type RuntimePresentation,
} from "../../agent/runtimes";
import { AssistantText } from "./AssistantText";
import { EventTimelineRow } from "./EventTimelineRow";
import {
  asString,
  displayVerb,
  isDiffTool,
  isEditTool,
  summaryStat,
} from "./toolPresentation";
import { getDisplayPath } from "../../agent/runtimes/shared";
import { ToolDetail } from "./ToolDetail";

/** 正常 turn 全部默认预览；只在异常大的 turn 中保留最近一小段详情。 */
export const EXTREME_DIFF_THRESHOLD = 32;
export const EXTREME_AUTO_OPEN_DIFF_DETAILS = 4;
type ExtremeMode = "normal" | "preserve-reading" | "compact";

function diffPath(item: ToolItem, workdir?: string): string | null {
  let path: string | null = null;
  if (item.toolName === "Write" || isEditTool(item.toolName)) {
    path = asString(item.input.file_path);
  } else if (item.toolName === "apply_patch") {
    const paths = item.input.paths;
    path = Array.isArray(paths) && typeof paths[0] === "string" ? paths[0] : null;
  }
  return path === null ? null : getDisplayPath(path, workdir);
}

/** 超大 turn 的旧文件工具保留逐条入口，但省掉完整 ToolBlock 的挂载成本。 */
function lightweightDiffTool(
  item: ToolItem,
  workdir: string | undefined,
  open: boolean,
  onToggle: () => void,
  key: string,
): ReactNode {
  const stat = summaryStat(item);
  const path = diffPath(item, workdir);
  return (
    <div
      className="cc-tool cc-tool--lightweight"
      data-status={item.status}
      key={key}
    >
      <button
        type="button"
        className="cc-tool__summary"
        aria-expanded={open}
        onClick={onToggle}
      >
        <span className="cc-tool__name">{displayVerb(item)}</span>
        {path && (
          <span className="cc-tool__brief">{path}</span>
        )}
        {stat && (
          <span className="cc-tool__stat">
            {stat.add > 0 && (
              <span className="cc-tool__stat-add">+{stat.add}</span>
            )}
            {stat.remove > 0 && (
              <span className="cc-tool__stat-rm">−{stat.remove}</span>
            )}
          </span>
        )}
        <span className="cc-tool__sr-only" aria-label="完成">完成</span>
      </button>
      {open && (
        <div className="cc-tool__detail">
          <ToolDetail item={item} workdir={workdir} />
        </div>
      )}
    </div>
  );
}

interface EventTimelineProps {
  readonly items: readonly TurnItem[];
  readonly onRetryLast?: () => void;
  // 历史中缺少 tool_result 的 Agent 也必须显示为已结束。
  readonly isReplay?: boolean;
  readonly onAnswer?: (answers: Record<string, string>) => void;
  readonly onCancel?: () => void;
  readonly onApprovalDecision?: (requestId: string, decision: string) => void;
  readonly workdir?: string;
  readonly presentation?: RuntimePresentation;
  readonly sessionId?: string;
  readonly codexSubagents?: PerSessionState["codexSubagents"];
  readonly onOpenSubagent?: (threadId: string) => void;
  readonly allowExtremeCompaction?: boolean;
}

export function EventTimeline({
  items,
  onRetryLast,
  isReplay,
  onAnswer,
  onCancel,
  onApprovalDecision,
  workdir,
  presentation,
  sessionId,
  codexSubagents,
  onOpenSubagent,
  allowExtremeCompaction = false,
}: EventTimelineProps) {
  // 必须返回 Fragment，消息块需保持为 .cc-msg__body 的直接子元素。
  const blocks: ReactNode[] = [];
  const diffToolCount = items.filter(
    (item) => item.kind === "tool" && isDiffTool(item.toolName),
  ).length;
  // 正在屏幕上逐步增长的 turn 不会突然换样式；只在返回/恢复一个已经很大的
  // turn 时先把旧 Markdown 作为轻量原文挂载，避免重建上百棵 Markdown 子树。
  const [extremeMode, setExtremeMode] = useState<ExtremeMode>(
    () => diffToolCount > EXTREME_DIFF_THRESHOLD ? "compact" : "normal",
  );
  const [renderDeferredMarkdown, setRenderDeferredMarkdown] = useState(
    () => diffToolCount <= EXTREME_DIFF_THRESHOLD,
  );
  const [openedOlderDiffs, setOpenedOlderDiffs] = useState<ReadonlySet<string>>(
    () => new Set(),
  );
  useEffect(() => {
    if (
      diffToolCount <= EXTREME_DIFF_THRESHOLD ||
      extremeMode === "compact"
    ) {
      return;
    }
    if (allowExtremeCompaction) {
      setExtremeMode("compact");
      if (extremeMode === "normal") setRenderDeferredMarkdown(false);
    } else if (extremeMode === "normal") {
      // 用户正在旧内容处阅读：保留既有详情，只把阈值后的新增内容轻量化。
      setExtremeMode("preserve-reading");
      setRenderDeferredMarkdown(false);
    }
  }, [allowExtremeCompaction, diffToolCount, extremeMode]);
  let deferredTextBoundary = 0;
  if (extremeMode === "compact") {
    let remaining = EXTREME_AUTO_OPEN_DIFF_DETAILS;
    for (let index = items.length - 1; index >= 0; index -= 1) {
      const item = items[index];
      if (item.kind !== "tool" || !isDiffTool(item.toolName)) continue;
      remaining -= 1;
      if (remaining === 0) {
        deferredTextBoundary = index;
        break;
      }
    }
  } else if (extremeMode === "preserve-reading") {
    let seen = 0;
    for (let index = 0; index < items.length; index += 1) {
      const item = items[index];
      if (item.kind !== "tool" || !isDiffTool(item.toolName)) continue;
      seen += 1;
      if (seen === EXTREME_DIFF_THRESHOLD) {
        deferredTextBoundary = index;
        break;
      }
    }
  }
  if (extremeMode !== "normal") {
    const deferredLabel =
      extremeMode === "compact" ? "较早" : "后续";
    blocks.push(
      <button
        type="button"
        className="cc-timeline__older-text-toggle"
        key="older-text-toggle"
        aria-pressed={renderDeferredMarkdown}
        onClick={() => setRenderDeferredMarkdown((rendered) => !rendered)}
      >
        {renderDeferredMarkdown
          ? `${deferredLabel}文字使用轻量显示`
          : `渲染${deferredLabel} Markdown`}
      </button>,
    );
  }
  let diffToolIndex = 0;
  let textBuf = "";
  let textBufDeferred = false;
  let key = 0;
  const flushText = () => {
    if (textBuf !== "") {
      if (textBufDeferred) {
        blocks.push(
          <div className="cc-md cc-md--deferred" key={`t${key++}`}>
            {textBuf}
          </div>,
        );
      } else {
        blocks.push(
          <AssistantText
            key={`t${key++}`}
            text={textBuf}
            sessionId={sessionId}
            workdir={workdir}
          />,
        );
      }
      textBuf = "";
      textBufDeferred = false;
    }
  };
  for (let index = 0; index < items.length; index += 1) {
    const item = items[index];
    if (item.kind === "text") {
      const deferThisText =
        extremeMode !== "normal" &&
        !renderDeferredMarkdown &&
        (extremeMode === "compact"
          ? index < deferredTextBoundary
          : index > deferredTextBoundary);
      if (textBuf && textBufDeferred !== deferThisText) flushText();
      textBufDeferred = deferThisText;
      // 相邻文本用空行连接，避免 Markdown 段落被合并。
      textBuf = textBuf ? `${textBuf}\n\n${item.text}` : item.text;
    } else if (
      presentation?.timelinePresenters.groupExplorationCommands &&
      item.kind === "tool" &&
      item.toolName === "command" &&
      isCodexExploration(item)
    ) {
      flushText();
      const exploration: ToolItem[] = [item];
      while (index + 1 < items.length) {
        const next = items[index + 1];
        if (
          next.kind !== "tool" ||
          next.toolName !== "command" ||
          !isCodexExploration(next)
        ) break;
        exploration.push(next);
        index += 1;
      }
      blocks.push(
        <CodexExplorationGroup
          key={`x${key++}`}
          items={exploration}
          workdir={workdir}
        />,
      );
    } else {
      flushText();
      let suppressDiffAutoOpen = false;
      if (item.kind === "tool" && isDiffTool(item.toolName)) {
        const ordinal = diffToolIndex++;
        suppressDiffAutoOpen =
          (extremeMode === "compact" &&
            ordinal < diffToolCount - EXTREME_AUTO_OPEN_DIFF_DETAILS) ||
          (extremeMode === "preserve-reading" &&
            ordinal >= EXTREME_DIFF_THRESHOLD);
      }
      if (
        suppressDiffAutoOpen &&
        extremeMode !== "normal" &&
        item.kind === "tool" &&
        item.status === "done"
      ) {
        const open = openedOlderDiffs.has(item.toolUseId);
        blocks.push(
          lightweightDiffTool(
            item,
            workdir,
            open,
            () => {
              setOpenedOlderDiffs((current) => {
                const next = new Set(current);
                if (next.has(item.toolUseId)) next.delete(item.toolUseId);
                else next.add(item.toolUseId);
                return next;
              });
            },
            `p${key++}`,
          ),
        );
        continue;
      }
      blocks.push(
        <EventTimelineRow
          key={`p${key++}`}
          item={item}
          onRetryLast={onRetryLast}
          isReplay={isReplay}
          onAnswer={onAnswer}
          onCancel={onCancel}
          onApprovalDecision={onApprovalDecision}
          workdir={workdir}
          presentation={presentation}
          codexSubagents={codexSubagents}
          onOpenSubagent={onOpenSubagent}
          thinkingComplete={
            item.kind === "thinking" && (Boolean(isReplay) || index < items.length - 1)
          }
          suppressDiffAutoOpen={suppressDiffAutoOpen}
        />,
      );
    }
  }
  flushText();
  if (blocks.length === 0) return null;
  return <Fragment>{blocks}</Fragment>;
}
