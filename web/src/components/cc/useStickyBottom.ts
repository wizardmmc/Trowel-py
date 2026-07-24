import { useCallback, useEffect, useRef, useState } from "react";

const THRESHOLD_PX = 32;

export interface StickyBottom {
  readonly sticky: boolean;
  readonly unread: number;
  readonly stickyRef: React.MutableRefObject<boolean>;
  readonly jumpToBottom: () => void;
}

/** 用户离开底部后停止自动跟随，并按新增 turn 计算未读数。 */
export function useStickyBottom(
  scrollRef: React.RefObject<HTMLElement | null>,
  turnsCount: number,
): StickyBottom {
  const [sticky, setSticky] = useState(true);
  const [unread, setUnread] = useState(0);
  const stickyRef = useRef(true);
  const turnsCountRef = useRef(turnsCount);
  const leftAtTurnsRef = useRef(0);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const onScroll = () => {
      const atBottom =
        el.scrollHeight - el.scrollTop - el.clientHeight <= THRESHOLD_PX;
      if (atBottom) {
        if (!stickyRef.current) {
          stickyRef.current = true;
          setSticky(true);
          setUnread(0);
        }
      } else if (stickyRef.current) {
        stickyRef.current = false;
        leftAtTurnsRef.current = turnsCountRef.current;
        setSticky(false);
      }
    };
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => el.removeEventListener("scroll", onScroll);
  }, [scrollRef]);

  useEffect(() => {
    turnsCountRef.current = turnsCount;
    if (!stickyRef.current) {
      setUnread(Math.max(0, turnsCount - leftAtTurnsRef.current));
    }
  }, [turnsCount]);

  const jumpToBottom = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    if (typeof el.scrollTo === "function") {
      el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
    }
    stickyRef.current = true;
    setSticky(true);
    setUnread(0);
  }, [scrollRef]);

  return { sticky, unread, stickyRef, jumpToBottom };
}
