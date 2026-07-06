import { useEffect, useRef, useState } from "react";
import type { RefObject } from "react";

/**
 * Track an element's pixel height via ResizeObserver.
 *
 * slice-032: used to drive `.cc-view__scroll`'s `scroll-padding-bottom` from
 * the Composer's live height, so the ✻ thinking row stays visible above the
 * Composer without a hardcoded constant that drifts when the Composer's size
 * changes (more input rows, elicit box, locale font metrics, …).
 *
 * Returns a ref to attach and the current height in px (0 until first measure).
 */
export function useElementHeight<T extends HTMLElement>(): [
  RefObject<T | null>,
  number,
] {
  const ref = useRef<T>(null);
  const [height, setHeight] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (el === null) return;
    const measure = (): void => setHeight(el.offsetHeight);
    measure();
    // jsdom has no ResizeObserver — measure once and bail (tests don't need
    // live tracking; offsetHeight is 0 there anyway).
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, height];
}
