import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

const THRESHOLD_PX = 32;

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

  useLayoutEffect(() => {
    turnsCountRef.current = turnsCount;
    if (currentSessionKeyRef.current !== sessionKey) {
      currentSessionKeyRef.current = sessionKey;
      stickyRef.current = true;
      leftAtTurnsRef.current = turnsCount;
    }
  }, [sessionKey, turnsCount]);

  const pauseFollowing = useCallback(() => {
    if (!stickyRef.current) return;
    stickyRef.current = false;
    leftAtTurnsRef.current = turnsCountRef.current;
    setViewState({ sessionKey, sticky: false, unread: 0 });
  }, [sessionKey]);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const onScroll = () => {
      const atBottom =
        el.scrollHeight - el.scrollTop - el.clientHeight <= THRESHOLD_PX;
      if (atBottom) {
        if (!stickyRef.current) {
          stickyRef.current = true;
          setViewState({ sessionKey, sticky: true, unread: 0 });
        }
      } else {
        pauseFollowing();
      }
    };
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => el.removeEventListener("scroll", onScroll);
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
