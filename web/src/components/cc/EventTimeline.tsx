import { Fragment, type ReactNode } from "react";

import type { ToolItem, TurnItem } from "../../stores/ccStore";
import { AssistantText } from "./AssistantText";
import { CodexExplorationGroup } from "./CodexExplorationGroup";
import { EventTimelineRow } from "./EventTimelineRow";
import { isCodexExploration } from "./codexCommandPresentation";

interface EventTimelineProps {
  readonly items: readonly TurnItem[];
  readonly onRetryLast?: () => void;
  // 历史中缺少 tool_result 的 Agent 也必须显示为已结束。
  readonly isReplay?: boolean;
  readonly onAnswer?: (answers: Record<string, string>) => void;
  readonly onCancel?: () => void;
  readonly onApprovalDecision?: (requestId: string, decision: string) => void;
  readonly workdir?: string;
  readonly runtime?: string;
}

export function EventTimeline({
  items,
  onRetryLast,
  isReplay,
  onAnswer,
  onCancel,
  onApprovalDecision,
  workdir,
  runtime,
}: EventTimelineProps) {
  // 必须返回 Fragment，消息块需保持为 .cc-msg__body 的直接子元素。
  const blocks: ReactNode[] = [];
  let textBuf = "";
  let key = 0;
  const flushText = () => {
    if (textBuf !== "") {
      blocks.push(<AssistantText key={`t${key++}`} text={textBuf} />);
      textBuf = "";
    }
  };
  for (let index = 0; index < items.length; index += 1) {
    const item = items[index];
    if (item.kind === "text") {
      // 相邻文本用空行连接，避免 Markdown 段落被合并。
      textBuf = textBuf ? `${textBuf}\n\n${item.text}` : item.text;
    } else if (
      runtime === "codex" &&
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
          runtime={runtime}
          thinkingComplete={
            item.kind === "thinking" && (Boolean(isReplay) || index < items.length - 1)
          }
        />,
      );
    }
  }
  flushText();
  if (blocks.length === 0) return null;
  return <Fragment>{blocks}</Fragment>;
}
