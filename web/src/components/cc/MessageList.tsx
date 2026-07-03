import { useEffect, useRef } from "react";

import type { Turn } from "../../stores/ccStore";
import { EventTimeline } from "./EventTimeline";
import { SpinnerLine } from "./SpinnerLine";

/**
 * The dialogue stream: one card per turn (user bubble + CC's response).
 *
 * Layout follows the spec's "A 混合": user + assistant *text* render as
 * shallow rounded cards (garden/bg-card, "你/CC" tag top-left, same skin as
 * ReviewModal); process events (thinking/tool/retrying/…) are bare rows
 * sandwiched between cards via EventTimeline. No left/right chat bubbles.
 *
 * Assistant text items inside a turn render as one card; the process timeline
 * sits beside/within it. aria-live is polite and we only announce completed
 * text, so screen readers don't get chatter per delta.
 */
interface MessageListProps {
  readonly turns: readonly Turn[];
  readonly streaming: boolean;
  readonly onRetryLast?: () => void;
}

/** A single piece of assistant text, rendered as markdown-ish paragraphs. */
function AssistantText({ text }: { readonly text: string }) {
  return (
    <div className="cc-msg__assistant-text">
      {text.split(/\n{2,}/).map((para, i) => (
        <p key={i}>{para}</p>
      ))}
    </div>
  );
}

function TurnCard({
  turn,
  onRetryLast,
}: {
  readonly turn: Turn;
  readonly onRetryLast?: () => void;
}) {
  const textItems = turn.items.filter((i) => i.kind === "text");
  const assistantText = textItems.map((i) => (i.kind === "text" ? i.text : "")).join("");
  return (
    <div className="cc-turn" data-turn-status={turn.status}>
      <div className="cc-msg cc-msg--user">
        <span className="cc-msg__tag">你</span>
        <div className="cc-msg__body">{turn.userText}</div>
      </div>
      {(assistantText || turn.items.some((i) => i.kind !== "text")) && (
        <div className="cc-msg cc-msg--assistant">
          <span className="cc-msg__tag">CC</span>
          <div className="cc-msg__body">
            {assistantText && <AssistantText text={assistantText} />}
            <EventTimeline
              items={turn.items}
              onRetryLast={onRetryLast}
              isReplay={turn.status !== "active"}
            />
          </div>
        </div>
      )}
    </div>
  );
}

export function MessageList({ turns, streaming, onRetryLast }: MessageListProps) {
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
        <TurnCard key={turn.id} turn={turn} onRetryLast={onRetryLast} />
      ))}
      {/* slice-025-a A1: the ✻ thinking… row rides the tail of the stream.
        Renders null outside the thinking phase (see SpinnerLine). */}
      <SpinnerLine />
      <div ref={endRef} />
    </div>
  );
}
