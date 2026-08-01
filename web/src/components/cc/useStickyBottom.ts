/** 管理消息列表自动跟随、用户离底和未读 turn 计数。 */

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

const THRESHOLD_PX = 32;

function isTranscriptNavigationTarget(
  container: HTMLElement,
  target: EventTarget | null,
): boolean {
  const ownerDocument = container.ownerDocument;
  if (target === ownerDocument.body || target === ownerDocument.documentElement) {
    const dialogs = ownerDocument.querySelectorAll<HTMLElement>(
      "[role='dialog'], [aria-modal='true'], dialog[open]",
    );
    return !Array.from(dialogs).some((dialog) => {
      if (dialog.hidden) return false;
      const style = ownerDocument.defaultView?.getComputedStyle(dialog);
      return style?.display !== "none" && style?.visibility !== "hidden";
    });
  }
  if (!(target instanceof HTMLElement) || !container.contains(target)) {
    return false;
  }
  return !target.closest(
    "input, textarea, select, [role='textbox'], [contenteditable]:not([contenteditable='false'])",
  );
}

export interface StickyBottom {
  readonly sticky: boolean;
  readonly unread: number;
  readonly stickyRef: React.MutableRefObject<boolean>;
  readonly pauseFollowing: () => void;
  readonly jumpToBottom: () => void;
}

/** 用户离开底部后停止自动跟随，并按新增 turn 计算未读数。 */
export function useStickyBottom(
  scrollRef: React.RefObject<HTMLElement | null>,
  turnsCount: number,
  sessionKey?: string | null,
): StickyBottom {
  const [viewState, setViewState] = useState({
    sessionKey,
    sticky: true,
    unread: 0,
  });
  const activeViewState =
    viewState.sessionKey === sessionKey
      ? viewState
      : { sessionKey, sticky: true, unread: 0 };
  const stickyRef = useRef(true);
  const turnsCountRef = useRef(turnsCount);
  const leftAtTurnsRef = useRef(0);
  const currentSessionKeyRef = useRef(sessionKey);
  const touchYRef = useRef<number | null>(null);
  const draggingScrollbarRef = useRef(false);
  const leavingBottomRef = useRef(false);

  useLayoutEffect(() => {
    turnsCountRef.current = turnsCount;
    if (currentSessionKeyRef.current !== sessionKey) {
      currentSessionKeyRef.current = sessionKey;
      stickyRef.current = true;
      leftAtTurnsRef.current = turnsCount;
      touchYRef.current = null;
      draggingScrollbarRef.current = false;
      leavingBottomRef.current = false;
      setViewState({ sessionKey, sticky: true, unread: 0 });
    }
  }, [sessionKey, turnsCount]);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el || typeof el.scrollTo !== "function") return;
    let settleFrame: number | null = null;
    const follow = () => {
      if (
        currentSessionKeyRef.current !== sessionKey ||
        !stickyRef.current
      ) {
        return;
      }
      el.scrollTo({ top: el.scrollHeight, behavior: "auto" });
    };
    const frame = window.requestAnimationFrame(() => {
      follow();
      settleFrame = window.requestAnimationFrame(follow);
    });
    return () => {
      window.cancelAnimationFrame(frame);
      if (settleFrame !== null) window.cancelAnimationFrame(settleFrame);
    };
  }, [scrollRef, sessionKey]);

  const pauseFollowing = useCallback(() => {
    if (!stickyRef.current) return;
    leavingBottomRef.current = true;
    stickyRef.current = false;
    leftAtTurnsRef.current = turnsCountRef.current;
    setViewState({ sessionKey, sticky: false, unread: 0 });
  }, [sessionKey]);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const atBottom = () =>
      el.scrollHeight - el.scrollTop - el.clientHeight <= THRESHOLD_PX;
    const resumeFollowing = () => {
      if (stickyRef.current) return;
      stickyRef.current = true;
      setViewState({ sessionKey, sticky: true, unread: 0 });
    };
    const onScroll = () => {
      if (atBottom()) {
        if (!stickyRef.current && !leavingBottomRef.current) {
          resumeFollowing();
        }
      } else {
        leavingBottomRef.current = false;
        if (draggingScrollbarRef.current) {
          pauseFollowing();
          leavingBottomRef.current = false;
        }
      }
    };
    const onWheel = (event: WheelEvent) => {
      if (!event.ctrlKey && event.deltaY < 0 && el.scrollTop > 0) {
        pauseFollowing();
      } else if (event.deltaY > 0) {
        leavingBottomRef.current = false;
      }
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (
        event.defaultPrevented ||
        !isTranscriptNavigationTarget(el, event.target) ||
        el.scrollTop <= 0
      ) {
        return;
      }
      const movesUp =
        event.key === "ArrowUp" ||
        event.key === "PageUp" ||
        event.key === "Home" ||
        (event.key === " " && event.shiftKey);
      if (movesUp) {
        pauseFollowing();
      } else if (
        event.key === "ArrowDown" ||
        event.key === "PageDown" ||
        event.key === "End" ||
        (event.key === " " && !event.shiftKey)
      ) {
        leavingBottomRef.current = false;
      }
    };
    const onTouchStart = (event: TouchEvent) => {
      touchYRef.current = event.touches[0]?.clientY ?? null;
    };
    const onTouchMove = (event: TouchEvent) => {
      const currentY = event.touches[0]?.clientY;
      const previousY = touchYRef.current;
      if (currentY === undefined || previousY === null) return;
      touchYRef.current = currentY;
      if (currentY > previousY && el.scrollTop > 0) {
        pauseFollowing();
      } else if (currentY < previousY) {
        leavingBottomRef.current = false;
      }
    };
    const onPointerDown = (event: PointerEvent) => {
      if (event.pointerType !== "mouse" || event.target !== el) return;
      const rect = el.getBoundingClientRect();
      const scrollbarWidth = Math.max(12, el.offsetWidth - el.clientWidth);
      draggingScrollbarRef.current = event.clientX >= rect.right - scrollbarWidth;
    };
    const stopScrollbarDrag = () => {
      const wasDragging = draggingScrollbarRef.current;
      draggingScrollbarRef.current = false;
      if (wasDragging && atBottom()) {
        leavingBottomRef.current = false;
        resumeFollowing();
      }
    };
    const ownerWindow = el.ownerDocument.defaultView;
    el.addEventListener("scroll", onScroll, { passive: true });
    el.addEventListener("wheel", onWheel, { passive: true });
    el.addEventListener("touchstart", onTouchStart, { passive: true });
    el.addEventListener("touchmove", onTouchMove, { passive: true });
    el.addEventListener("pointerdown", onPointerDown, { passive: true });
    ownerWindow?.addEventListener("pointerup", stopScrollbarDrag, {
      passive: true,
    });
    ownerWindow?.addEventListener("pointercancel", stopScrollbarDrag, {
      passive: true,
    });
    ownerWindow?.addEventListener("keydown", onKeyDown);
    return () => {
      el.removeEventListener("scroll", onScroll);
      el.removeEventListener("wheel", onWheel);
      el.removeEventListener("touchstart", onTouchStart);
      el.removeEventListener("touchmove", onTouchMove);
      el.removeEventListener("pointerdown", onPointerDown);
      ownerWindow?.removeEventListener("pointerup", stopScrollbarDrag);
      ownerWindow?.removeEventListener("pointercancel", stopScrollbarDrag);
      ownerWindow?.removeEventListener("keydown", onKeyDown);
    };
  }, [pauseFollowing, scrollRef, sessionKey]);

  useEffect(() => {
    if (!stickyRef.current) {
      setViewState({
        sessionKey,
        sticky: false,
        unread: Math.max(0, turnsCount - leftAtTurnsRef.current),
      });
    }
  }, [sessionKey, turnsCount]);

  const jumpToBottom = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    leavingBottomRef.current = false;
    if (typeof el.scrollTo === "function") {
      el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
    }
    stickyRef.current = true;
    setViewState({ sessionKey, sticky: true, unread: 0 });
  }, [scrollRef, sessionKey]);

  return {
    sticky: activeViewState.sticky,
    unread: activeViewState.unread,
    stickyRef,
    pauseFollowing,
    jumpToBottom,
  };
}
