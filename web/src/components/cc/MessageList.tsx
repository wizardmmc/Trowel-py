import { useEffect, useRef, type MutableRefObject } from "react";

import type { Turn } from "../../stores/ccStore";
import { formatRunDuration } from "./durationLabel";
import { EventTimeline } from "./EventTimeline";
import { scrubUserText } from "./scrubUserText";
import { SpinnerLine } from "./SpinnerLine";

interface MessageListProps {
  readonly turns: readonly Turn[];
  readonly streaming: boolean;
  readonly phase?: string;
  readonly stickyRef?: MutableRefObject<boolean>;
  readonly onRetryLast?: () => void;
  readonly onAnswer?: (answers: Record<string, string>) => void;
  readonly onCancel?: () => void;
  readonly onApprovalDecision?: (requestId: string, decision: string) => void;
  readonly onRevert?: (turn: Turn) => void;
  readonly workdir?: string;
  readonly runtime?: string;
}

function runtimeLabel(runtime?: string): "Codex" | "CC" | "Agent" {
  if (runtime === "codex") return "Codex";
  if (runtime === "claude_code") return "CC";
  return "Agent";
}

function TurnCard({
  turn,
  streaming,
  onRetryLast,
  onAnswer,
  onCancel,
  onApprovalDecision,
  onRevert,
  workdir,
  runtime,
}: {
  readonly turn: Turn;
  readonly streaming: boolean;
  readonly onRetryLast?: () => void;
  readonly onAnswer?: (answers: Record<string, string>) => void;
  readonly onCancel?: () => void;
  readonly onApprovalDecision?: (requestId: string, decision: string) => void;
  readonly onRevert?: (turn: Turn) => void;
  readonly workdir?: string;
  readonly runtime?: string;
}) {
  const hasContent = turn.items.length > 0;
  const canRevert = turn.revertible && turn.turnId !== null && !streaming;
  const cleanedUserText = scrubUserText(turn.userText ?? "");
  return (
    <div className="cc-turn" data-turn-status={turn.status}>
      {canRevert && (
        <button
          type="button"
          className="cc-turn__revert"
          title="回滚到这轮之前"
          onClick={() => onRevert?.(turn)}
        >
          <span aria-hidden>↶</span> 回滚到这里
        </button>
      )}
      {cleanedUserText && (
        <div className="cc-msg cc-msg--user">
          <span className="cc-msg__tag">你</span>
          <div className="cc-msg__body">{cleanedUserText}</div>
        </div>
      )}
      {hasContent && (
        <div className="cc-msg cc-msg--assistant">
          <span className="cc-msg__tag">{runtimeLabel(runtime)}</span>
          <div className="cc-msg__body">
            <EventTimeline
              items={turn.items}
              onRetryLast={onRetryLast}
              isReplay={turn.status !== "active"}
              onAnswer={onAnswer}
              onCancel={onCancel}
              onApprovalDecision={onApprovalDecision}
              workdir={workdir}
              runtime={runtime}
            />
          </div>
        </div>
      )}
      {turn.status === "done" &&
        turn.durationSeconds != null &&
        turn.durationSeconds > 0 && (
          <div
            className="cc-turn__duration"
            aria-label={`本轮用时 ${turn.durationSeconds} 秒`}
          >
            Ran for {formatRunDuration(turn.durationSeconds)}
          </div>
        )}
    </div>
  );
}

export function MessageList({
  turns,
  streaming,
  phase,
  stickyRef,
  onRetryLast,
  onAnswer,
  onCancel,
  onApprovalDecision,
  onRevert,
  workdir,
  runtime,
}: MessageListProps) {
  const endRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (stickyRef && !stickyRef.current) return;
    if (typeof endRef.current?.scrollIntoView === "function") {
      endRef.current.scrollIntoView({ behavior: "smooth", block: "end" });
    }
  }, [turns, streaming, phase, stickyRef]);

  if (turns.length === 0) {
    return (
      <div className="cc-empty" data-testid="cc-empty">
        <p>输入一条消息开始与 {runtimeLabel(runtime)} 对话。</p>
      </div>
    );
  }

  return (
    <div className="cc-msglist" role="log" aria-live="polite" aria-busy={streaming}>
      {turns.map((turn) => (
        <TurnCard
          key={turn.id}
          turn={turn}
          streaming={streaming}
          onRetryLast={onRetryLast}
          onAnswer={onAnswer}
          onCancel={onCancel}
          onApprovalDecision={onApprovalDecision}
          onRevert={onRevert}
          workdir={workdir}
          runtime={runtime}
        />
      ))}
      <SpinnerLine />
      <div ref={endRef} />
    </div>
  );
}
