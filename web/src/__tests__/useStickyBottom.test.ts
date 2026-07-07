import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { useStickyBottom } from "../components/cc/useStickyBottom";

/** Build a fake scroll container with jsdom-mockable layout props. */
function makeScrollRef(scrollHeight: number, clientHeight: number) {
  const div = document.createElement("div");
  let scrollTop = 0;
  Object.defineProperty(div, "scrollHeight", {
    configurable: true,
    get: () => scrollHeight,
  });
  Object.defineProperty(div, "clientHeight", {
    configurable: true,
    get: () => clientHeight,
  });
  Object.defineProperty(div, "scrollTop", {
    configurable: true,
    get: () => scrollTop,
    set: (v: number) => {
      scrollTop = v;
    },
  });
  document.body.appendChild(div);
  return {
    ref: { current: div },
    div,
    setScrollTop: (v: number) => {
      scrollTop = v;
    },
  };
}

describe("useStickyBottom (slice-035 bug2)", () => {
  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("starts sticky at the bottom with zero unread", () => {
    const { ref } = makeScrollRef(1000, 500);
    const { result } = renderHook(() => useStickyBottom(ref, 1));
    expect(result.current.sticky).toBe(true);
    expect(result.current.unread).toBe(0);
    expect(result.current.stickyRef.current).toBe(true);
  });

  it("flips sticky=false when scrolled away from the bottom", () => {
    const { ref, div, setScrollTop } = makeScrollRef(1000, 500);
    const { result } = renderHook(() => useStickyBottom(ref, 1));
    setScrollTop(0); // far from bottom
    act(() => div.dispatchEvent(new Event("scroll")));
    expect(result.current.sticky).toBe(false);
    expect(result.current.stickyRef.current).toBe(false);
  });

  it("unread grows as new turns arrive while away from bottom", () => {
    const { ref, div, setScrollTop } = makeScrollRef(1000, 500);
    const { result, rerender } = renderHook(
      ({ n }) => useStickyBottom(ref, n),
      { initialProps: { n: 1 } },
    );
    setScrollTop(0);
    act(() => div.dispatchEvent(new Event("scroll")));
    expect(result.current.sticky).toBe(false);
    rerender({ n: 3 });
    expect(result.current.unread).toBe(2);
  });

  it("scrolling back to bottom re-arms sticky and clears unread", () => {
    const { ref, div, setScrollTop } = makeScrollRef(1000, 500);
    const { result, rerender } = renderHook(
      ({ n }) => useStickyBottom(ref, n),
      { initialProps: { n: 1 } },
    );
    setScrollTop(0);
    act(() => div.dispatchEvent(new Event("scroll")));
    rerender({ n: 3 });
    expect(result.current.unread).toBe(2);
    setScrollTop(500); // 1000 - 500 - 500 = 0 <= threshold → at bottom
    act(() => div.dispatchEvent(new Event("scroll")));
    expect(result.current.sticky).toBe(true);
    expect(result.current.unread).toBe(0);
  });

  it("jumpToBottom re-arms sticky and clears unread", () => {
    const { ref, div, setScrollTop } = makeScrollRef(1000, 500);
    const { result, rerender } = renderHook(
      ({ n }) => useStickyBottom(ref, n),
      { initialProps: { n: 1 } },
    );
    setScrollTop(0);
    act(() => div.dispatchEvent(new Event("scroll")));
    rerender({ n: 3 });
    expect(result.current.unread).toBe(2);
    act(() => result.current.jumpToBottom());
    expect(result.current.sticky).toBe(true);
    expect(result.current.unread).toBe(0);
  });
});
