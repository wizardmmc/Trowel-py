import { useCallback, useEffect, useRef, useState } from "react";

/**
 * slice-035 bug2: sticky-bottom (auto-follow) for the dialogue scroll area.
 *
 * Behavior:
 * - When the user is near the bottom, `sticky` is true and new content
 *   auto-scrolls into view (the list follows the stream).
 * - When the user scrolls up to read history, `sticky` flips false and new
 *   content stops yanking the viewport back down.
 * - A "回最新" button (rendered by SessionView when `sticky` is false) calls
 *   `jumpToBottom()` to smooth-scroll back and re-arm stickiness.
 * - `unread` counts turns that arrived while away from the bottom (for the
 *   button's badge).
 *
 * `stickyRef` mirrors `sticky` as a ref so MessageList's scroll effect can
 * read the latest value without resubscribing each flip.
 */

/** Within this many px of the bottom counts as "at the bottom". */
const THRESHOLD_PX = 32;

export interface StickyBottom {
  /** Whether the viewport currently follows the bottom (re-renders on flip). */
  readonly sticky: boolean;
  /** Turns that arrived while away from the bottom (badge count). */
  readonly unread: number;
  /** Latest `sticky` as a ref (for effects that must not resubscribe). */
  readonly stickyRef: React.MutableRefObject<boolean>;
  /** Smooth-scroll to the bottom and re-arm stickiness / clear unread. */
  readonly jumpToBottom: () => void;
}

/**
 * Track sticky-bottom state for a scroll container.
 *
 * Args:
 *   scrollRef: ref to the scrollable element (.cc-view__scroll).
 *   turnsCount: current turn count (drives unread while away from bottom).
 */
export function useStickyBottom(
  scrollRef: React.RefObject<HTMLElement | null>,
  turnsCount: number,
): StickyBottom {
  const [sticky, setSticky] = useState(true);
  const [unread, setUnread] = useState(0);
  const stickyRef = useRef(true);
  const turnsCountRef = useRef(turnsCount);
  const leftAtTurnsRef = useRef(0);

  // Scroll listener: flip sticky as the user crosses the bottom threshold.
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
        // Just left the bottom — record the turn baseline for unread.
        stickyRef.current = false;
        leftAtTurnsRef.current = turnsCountRef.current;
        setSticky(false);
      }
    };
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => el.removeEventListener("scroll", onScroll);
  }, [scrollRef]);

  // Track turnsCount; while away from the bottom, unread = new turns since leaving.
  useEffect(() => {
    turnsCountRef.current = turnsCount;
    if (!stickyRef.current) {
      setUnread(Math.max(0, turnsCount - leftAtTurnsRef.current));
    }
  }, [turnsCount]);

  const jumpToBottom = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    // jsdom has no scrollTo; guard so tests don't blow up. Sticky/unread
    // still update regardless.
    if (typeof el.scrollTo === "function") {
      el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
    }
    stickyRef.current = true;
    setSticky(true);
    setUnread(0);
  }, [scrollRef]);

  return { sticky, unread, stickyRef, jumpToBottom };
}
