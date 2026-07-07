import { useEffect, useRef, type MutableRefObject } from "react";

import type { Turn } from "../../stores/ccStore";
import { EventTimeline } from "./EventTimeline";
import { scrubUserText } from "./scrubUserText";
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
  /** slice-032: active session phase. The auto-scroll effect re-fires when the
   * thinking row appears (phase flips to "thinking") — without this, the row
   * renders but the effect (deps [turns, streaming]) never re-runs because
   * `turns` is unchanged and `streaming` was already true, so it stays hidden
   * behind the Composer. */
  readonly phase?: string;
  /** slice-035 bug2: when false, the list does NOT auto-follow the stream —
   * the user scrolled up to read history. From useStickyBottom in SessionView. */
  readonly stickyRef?: MutableRefObject<boolean>;
  readonly onRetryLast?: () => void;
  readonly onAnswer?: (answers: Record<string, string>) => void;
  readonly onCancel?: () => void;
  /** slice-026: request to revert a turn — opens the confirm modal. Called
   * only for revertible turns while CC is idle. */
  readonly onRevert?: (turn: Turn) => void;
  /** slice-029: the session's cwd, so Edit/Write paths render project-relative
   * (CC `getDisplayPath`) instead of full absolute. Optional — omit for tests. */
  readonly workdir?: string;
}

function TurnCard({
  turn,
  streaming,
  onRetryLast,
  onAnswer,
  onCancel,
  onRevert,
  workdir,
}: {
  readonly turn: Turn;
  readonly streaming: boolean;
  readonly onRetryLast?: () => void;
  readonly onAnswer?: (answers: Record<string, string>) => void;
  readonly onCancel?: () => void;
  readonly onRevert?: (turn: Turn) => void;
  readonly workdir?: string;
}) {
  const hasContent = turn.items.length > 0;
  const canRevert = turn.revertible && turn.turnId !== null && !streaming;
  // slice-035 bug4: defensive FE scrub — backend already cleaned the text, this
  // is a second line of defense. Empty after scrub = injection; hide the bubble.
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
          <span className="cc-msg__tag">CC</span>
          <div className="cc-msg__body">
            <EventTimeline
              items={turn.items}
              onRetryLast={onRetryLast}
              isReplay={turn.status !== "active"}
              onAnswer={onAnswer}
              onCancel={onCancel}
              workdir={workdir}
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
  phase,
  stickyRef,
  onRetryLast,
  onAnswer,
  onCancel,
  onRevert,
  workdir,
}: MessageListProps) {
  const endRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    // slice-035 bug2: only auto-follow the stream when sticky (user at the
    // bottom). When the user scrolled up to read history, don't yank them back.
    if (stickyRef && !stickyRef.current) return;
    // jsdom has no scrollIntoView; guard so tests don't blow up.
    if (typeof endRef.current?.scrollIntoView === "function") {
      endRef.current.scrollIntoView({ behavior: "smooth", block: "end" });
    }
  }, [turns, streaming, phase, stickyRef]);

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
          streaming={streaming}
          onRetryLast={onRetryLast}
          onAnswer={onAnswer}
          onCancel={onCancel}
          onRevert={onRevert}
          workdir={workdir}
        />
      ))}
      {/* slice-025-a A1: the ✻ thinking… row rides the tail of the stream.
        Renders null outside the thinking phase (see SpinnerLine). */}
      <SpinnerLine />
      <div ref={endRef} />
    </div>
  );
}
