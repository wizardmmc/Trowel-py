import { useEffect, useRef } from "react";

import type { Turn } from "../../stores/ccStore";
import { EventTimeline } from "./EventTimeline";
import { SpinnerLine } from "./SpinnerLine";

/**
 * The dialogue stream: one card per turn (user bubble + CC's response).
 *
 * Layout = spec's "A 混合": user + assistant text are shallow rounded cards
 * (garden / bg-card, "你/CC" tag top-left); process events (thinking / tool /
 * retrying / …) and assistant text interleave *inside* the assistant card body
 * in true item order (slice-025-b B1). EventTimeline owns that interleaving:
 * consecutive text items merge into one AssistantText markdown block, process
 * items render as bare rows where they actually occurred. No more
 * text-bucketed-first / process-bucketed-after.
 *
 * aria-live is polite and we only announce completed text, so screen readers
 * don't get chatter per delta.
 */
interface MessageListProps {
  readonly turns: readonly Turn[];
  readonly streaming: boolean;
  readonly onRetryLast?: () => void;
  readonly onAnswer?: (answers: Record<string, string>) => void;
  readonly onCancel?: () => void;
}

function TurnCard({
  turn,
  onRetryLast,
  onAnswer,
  onCancel,
}: {
  readonly turn: Turn;
  readonly onRetryLast?: () => void;
  readonly onAnswer?: (answers: Record<string, string>) => void;
  readonly onCancel?: () => void;
}) {
  const hasContent = turn.items.length > 0;
  return (
    <div className="cc-turn" data-turn-status={turn.status}>
      <div className="cc-msg cc-msg--user">
        <span className="cc-msg__tag">你</span>
        <div className="cc-msg__body">{turn.userText}</div>
      </div>
      {hasContent && (
        <div className="cc-msg cc-msg--assistant">
          <span className="cc-msg__tag">CC</span>
          <div className="cc-msg__body">
            <EventTimeline
              items={turn.items}
              onRetryLast={onRetryLast}
              isReplay={turn.status !== "active"}
              onAnswer={onAnswer}
              onCancel={onCancel}
            />
          </div>
        </div>
      )}
    </div>
  );
}

export function MessageList({
  turns,
  streaming,
  onRetryLast,
  onAnswer,
  onCancel,
}: MessageListProps) {
  const endRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    // jsdom has no scrollIntoView; guard so tests don't blow up.
    if (typeof endRef.current?.scrollIntoView === "function") {
      endRef.current.scrollIntoView({ behavior: "smooth", block: "end" });
    }
  }, [turns, streaming]);

  if (turns.length === 0) {
    return (
      <div className="cc-empty" data-testid="cc-empty">
        <p>输入一条消息开始与 CC 对话。</p>
      </div>
    );
  }

  return (
    <div className="cc-msglist" role="log" aria-live="polite" aria-busy={streaming}>
      {turns.map((turn) => (
        <TurnCard
          key={turn.id}
          turn={turn}
          onRetryLast={onRetryLast}
          onAnswer={onAnswer}
          onCancel={onCancel}
        />
      ))}
      {/* slice-025-a A1: the ✻ thinking… row rides the tail of the stream.
        Renders null outside the thinking phase (see SpinnerLine). */}
      <SpinnerLine />
      <div ref={endRef} />
    </div>
  );
}
