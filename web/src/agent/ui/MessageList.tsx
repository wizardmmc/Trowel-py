/** 展示单个 Agent 会话的消息时间线，并管理大回合挂载和滚动定位。 */

import {
  memo,
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type RefObject,
} from "react";

import type { PerSessionState } from "../application";
import type { Turn } from "../domain";
import type { RuntimePresentation } from "../runtimes";
import { formatRunDuration } from "../../components/cc/durationLabel";
import { EventTimeline } from "../../components/cc/EventTimeline";
import { CurrentTurnContext } from "../../components/cc/CurrentTurnContext";
import { scrubUserText } from "../../components/cc/scrubUserText";
import { SpinnerLine } from "../../components/cc/SpinnerLine";

interface MessageListProps {
  readonly turns: readonly Turn[];
  readonly streaming: boolean;
  readonly phase?: string;
  readonly scrollRef?: RefObject<HTMLDivElement | null>;
  readonly sticky?: boolean;
  readonly followingRef?: RefObject<boolean>;
  readonly onLeaveBottom?: () => void;
  readonly onRetryLast?: () => void;
  readonly onAnswer?: (answers: Record<string, string>) => void;
  readonly onCancel?: () => void;
  readonly onApprovalDecision?: (requestId: string, decision: string) => void;
  readonly onRevert?: (turn: Turn) => void;
  readonly workdir?: string;
  readonly presentation?: RuntimePresentation;
  readonly sessionId?: string;
  readonly codexSubagents?: PerSessionState["codexSubagents"];
  readonly onOpenSubagent?: (threadId: string) => void;
  readonly emptyLabel?: string;
}

const INITIAL_VISIBLE_TURNS = 2;
const OLDER_TURN_BATCH = 5;
const LOAD_OLDER_THRESHOLD_PX = 24;
const CONTEXT_ACTIVATION_PX = 44;

const TurnCard = memo(function TurnCard({
  turn,
  turnIndex,
  streaming,
  onRetryLast,
  onAnswer,
  onCancel,
  onApprovalDecision,
  onRevert,
  workdir,
  presentation,
  sessionId,
  codexSubagents,
  onOpenSubagent,
  allowExtremeCompaction,
}: {
  readonly turn: Turn;
  readonly turnIndex: number;
  readonly streaming: boolean;
  readonly onRetryLast?: () => void;
  readonly onAnswer?: (answers: Record<string, string>) => void;
  readonly onCancel?: () => void;
  readonly onApprovalDecision?: (requestId: string, decision: string) => void;
  readonly onRevert?: (turn: Turn) => void;
  readonly workdir?: string;
  readonly presentation?: RuntimePresentation;
  readonly sessionId?: string;
  readonly codexSubagents?: PerSessionState["codexSubagents"];
  readonly onOpenSubagent?: (threadId: string) => void;
  readonly allowExtremeCompaction: boolean;
}) {
  const hasContent = turn.items.length > 0;
  const canRevert =
    turn.revertible &&
    turn.turnId !== null &&
    !streaming &&
    (presentation?.supports(
      "revert",
      turn.status === "active" ? "live" : "history",
    ) ?? true);
  const cleanedUserText = scrubUserText(turn.userText ?? "");
  return (
    <div
      className="cc-turn"
      data-turn-index={turnIndex}
      data-turn-status={turn.status}
    >
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
          <span className="cc-msg__tag">
            {presentation?.shortLabel ?? "Agent"}
          </span>
          <div className="cc-msg__body">
            <EventTimeline
              items={turn.items}
              onRetryLast={onRetryLast}
              isReplay={turn.status !== "active"}
              onAnswer={onAnswer}
              onCancel={onCancel}
              onApprovalDecision={onApprovalDecision}
              workdir={workdir}
              presentation={presentation}
              sessionId={sessionId}
              codexSubagents={codexSubagents}
              onOpenSubagent={onOpenSubagent}
              allowExtremeCompaction={allowExtremeCompaction}
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
});

export function MessageList({
  turns,
  streaming,
  phase,
  scrollRef,
  sticky = true,
  followingRef,
  onLeaveBottom,
  onRetryLast,
  onAnswer,
  onCancel,
  onApprovalDecision,
  onRevert,
  workdir,
  presentation,
  sessionId,
  codexSubagents,
  onOpenSubagent,
  emptyLabel,
}: MessageListProps) {
  const endRef = useRef<HTMLDivElement>(null);
  const [visibleStart, setVisibleStart] = useState(() =>
    Math.max(0, turns.length - INITIAL_VISIBLE_TURNS),
  );
  const [context, setContext] = useState({
    turnIndex: null as number | null,
    text: "",
    visible: false,
  });
  const visibleStartRef = useRef(visibleStart);
  const previousTurnCountRef = useRef(turns.length);
  const prependAnchorRef = useRef<{
    readonly scrollHeight: number;
    readonly scrollTop: number;
  } | null>(null);
  const loadingOlderRef = useRef(false);
  const touchStartYRef = useRef<number | null>(null);
  const followFrameRef = useRef<number | null>(null);
  const stickyStateRef = useRef(sticky);

  useLayoutEffect(() => {
    visibleStartRef.current = visibleStart;
  }, [visibleStart]);

  useLayoutEffect(() => {
    const previousCount = previousTurnCountRef.current;
    const previousLatestStart = Math.max(
      0,
      previousCount - INITIAL_VISIBLE_TURNS,
    );
    const nextLatestStart = Math.max(0, turns.length - INITIAL_VISIBLE_TURNS);
    setVisibleStart((current) => {
      if (turns.length < previousCount) {
        return Math.min(current, nextLatestStart);
      }
      if (
        turns.length > previousCount &&
        sticky &&
        current === previousLatestStart
      ) {
        return nextLatestStart;
      }
      return current;
    });
    previousTurnCountRef.current = turns.length;
  }, [sticky, turns.length]);

  useLayoutEffect(() => {
    const anchor = prependAnchorRef.current;
    const element = scrollRef?.current;
    if (!anchor || !element) return;
    if (typeof element.scrollTo === "function") {
      element.scrollTo({
        top: anchor.scrollTop + element.scrollHeight - anchor.scrollHeight,
        behavior: "auto",
      });
    }
    prependAnchorRef.current = null;
    loadingOlderRef.current = false;
  }, [scrollRef, visibleStart]);

  useLayoutEffect(() => {
    stickyStateRef.current = sticky;
    if (!sticky) {
      if (followFrameRef.current !== null) {
        window.cancelAnimationFrame(followFrameRef.current);
        followFrameRef.current = null;
      }
      return;
    }
    if (followFrameRef.current !== null) return;
    followFrameRef.current = window.requestAnimationFrame(() => {
      followFrameRef.current = null;
      if (!(followingRef?.current ?? stickyStateRef.current)) return;
      const element = scrollRef?.current;
      if (element && typeof element.scrollTo === "function") {
        element.scrollTo({ top: element.scrollHeight, behavior: "auto" });
      } else if (typeof endRef.current?.scrollIntoView === "function") {
        endRef.current.scrollIntoView({ behavior: "auto", block: "end" });
      }
    });
  }, [followingRef, phase, scrollRef, sticky, streaming, turns]);

  useEffect(
    () => () => {
      if (followFrameRef.current !== null) {
        window.cancelAnimationFrame(followFrameRef.current);
        followFrameRef.current = null;
      }
    },
    [],
  );

  const updateContext = useCallback(() => {
    const element = scrollRef?.current;
    if (!element) return;
    const nodes = Array.from(
      element.querySelectorAll<HTMLElement>(".cc-turn[data-turn-index]"),
    );
    if (nodes.length === 0) {
      setContext({ turnIndex: null, text: "", visible: false });
      return;
    }
    const containerRect = element.getBoundingClientRect();
    let currentNode = nodes[0];
    if (sticky) {
      currentNode = nodes[nodes.length - 1];
    } else {
      const activationLine = containerRect.top + CONTEXT_ACTIVATION_PX;
      for (const node of nodes) {
        if (node.getBoundingClientRect().top <= activationLine) {
          currentNode = node;
        } else {
          break;
        }
      }
    }
    const turnIndex = Number(currentNode.dataset.turnIndex);
    const userCard = currentNode.querySelector<HTMLElement>(".cc-msg--user");
    const userRect = userCard?.getBoundingClientRect();
    const sourceVisible = Boolean(
      userRect &&
        userRect.bottom > containerRect.top &&
        userRect.top < containerRect.bottom,
    );
    const visible = Boolean(
      userRect && !sourceVisible && userRect.bottom <= containerRect.top,
    );
    const text = scrubUserText(turns[turnIndex]?.userText ?? "");
    setContext((current) =>
      current.turnIndex === turnIndex &&
      current.text === text &&
      current.visible === visible
        ? current
        : { turnIndex, text, visible },
    );
  }, [scrollRef, sticky, turns]);

  useLayoutEffect(updateContext, [updateContext, visibleStart]);

  const loadOlder = useCallback(() => {
    const element = scrollRef?.current;
    if (
      !element ||
      element.scrollTop > LOAD_OLDER_THRESHOLD_PX ||
      visibleStartRef.current === 0 ||
      loadingOlderRef.current
    ) {
      return;
    }
    loadingOlderRef.current = true;
    prependAnchorRef.current = {
      scrollHeight: element.scrollHeight,
      scrollTop: element.scrollTop,
    };
    onLeaveBottom?.();
    setVisibleStart((current) =>
      Math.max(0, current - OLDER_TURN_BATCH),
    );
  }, [onLeaveBottom, scrollRef]);

  useEffect(() => {
    const element = scrollRef?.current;
    if (!element) return;
    const onScroll = () => {
      updateContext();
      loadOlder();
    };
    const onWheel = (event: WheelEvent) => {
      if (event.deltaY < 0) loadOlder();
    };
    const onTouchStart = (event: TouchEvent) => {
      touchStartYRef.current = event.touches[0]?.clientY ?? null;
    };
    const onTouchMove = (event: TouchEvent) => {
      const currentY = event.touches[0]?.clientY;
      const startY = touchStartYRef.current;
      if (currentY === undefined || startY === null || currentY <= startY) return;
      touchStartYRef.current = currentY;
      loadOlder();
    };
    element.addEventListener("scroll", onScroll, { passive: true });
    element.addEventListener("wheel", onWheel, { passive: true });
    element.addEventListener("touchstart", onTouchStart, { passive: true });
    element.addEventListener("touchmove", onTouchMove, { passive: true });
    return () => {
      element.removeEventListener("scroll", onScroll);
      element.removeEventListener("wheel", onWheel);
      element.removeEventListener("touchstart", onTouchStart);
      element.removeEventListener("touchmove", onTouchMove);
    };
  }, [loadOlder, scrollRef, updateContext]);

  const jumpToContext = useCallback(() => {
    const element = scrollRef?.current;
    if (
      !element ||
      typeof element.scrollTo !== "function" ||
      context.turnIndex === null
    ) {
      return;
    }
    const target = element.querySelector<HTMLElement>(
      `.cc-turn[data-turn-index="${context.turnIndex}"]`,
    );
    if (!target) return;
    onLeaveBottom?.();
    const containerRect = element.getBoundingClientRect();
    const targetRect = target.getBoundingClientRect();
    element.scrollTo({
      top: Math.max(
        0,
        element.scrollTop + targetRect.top - containerRect.top - 8,
      ),
      behavior: "smooth",
    });
  }, [context.turnIndex, onLeaveBottom, scrollRef]);

  if (turns.length === 0) {
    return (
      <div className="cc-empty" data-testid="cc-empty">
        <p>
          {emptyLabel ??
            `输入一条消息开始与 ${presentation?.shortLabel ?? "Agent"} 对话。`}
        </p>
      </div>
    );
  }

  return (
    <>
      <CurrentTurnContext
        text={context.text}
        visible={context.visible}
        onJump={jumpToContext}
      />
      <div className="cc-msglist" role="log" aria-live="polite" aria-busy={streaming}>
        {turns.slice(visibleStart).map((turn, index) => (
          <TurnCard
            key={turn.id}
            turn={turn}
            turnIndex={visibleStart + index}
            streaming={streaming}
            onRetryLast={onRetryLast}
            onAnswer={onAnswer}
            onCancel={onCancel}
            onApprovalDecision={onApprovalDecision}
            onRevert={onRevert}
            workdir={workdir}
            presentation={presentation}
            sessionId={sessionId}
            codexSubagents={codexSubagents}
            onOpenSubagent={onOpenSubagent}
            allowExtremeCompaction={sticky}
          />
        ))}
        <SpinnerLine />
        <div ref={endRef} />
      </div>
    </>
  );
}
