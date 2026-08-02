import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { useStickyBottom } from "../components/cc/useStickyBottom";

function makeScrollRef(
  scrollHeight: number,
  clientHeight: number,
  initialScrollTop = 0,
) {
  const div = document.createElement("div");
  let scrollTop = initialScrollTop;
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

function touchEvent(type: string, clientY: number): Event {
  const event = new Event(type);
  Object.defineProperty(event, "touches", {
    value: [{ clientY }],
  });
  return event;
}

function pointerEvent(type: string, clientX: number): Event {
  const event = new Event(type);
  Object.defineProperties(event, {
    pointerType: { value: "mouse" },
    clientX: { value: clientX },
  });
  return event;
}

describe("useStickyBottom", () => {
  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("starts sticky at the bottom with zero unread", () => {
    const { ref } = makeScrollRef(1000, 500, 500);
    const { result } = renderHook(() => useStickyBottom(ref, 1));
    expect(result.current.sticky).toBe(true);
    expect(result.current.unread).toBe(0);
    expect(result.current.stickyRef.current).toBe(true);
  });

  it("flips sticky=false when scrolled away from the bottom", () => {
    const { ref, div, setScrollTop } = makeScrollRef(1000, 500, 500);
    const { result } = renderHook(() => useStickyBottom(ref, 1));
    act(() => div.dispatchEvent(new WheelEvent("wheel", { deltaY: -40 })));
    setScrollTop(0);
    act(() => div.dispatchEvent(new Event("scroll")));
    expect(result.current.sticky).toBe(false);
    expect(result.current.stickyRef.current).toBe(false);
  });

  it("pauses following before PageUp moves the scroll container", () => {
    const { ref, div, setScrollTop } = makeScrollRef(1000, 500, 500);
    const transcriptButton = document.createElement("button");
    div.appendChild(transcriptButton);
    const { result } = renderHook(() => useStickyBottom(ref, 1));

    act(() =>
      transcriptButton.dispatchEvent(
        new KeyboardEvent("keydown", { key: "PageUp", bubbles: true }),
      ),
    );
    act(() => div.dispatchEvent(new Event("scroll")));
    expect(result.current.sticky).toBe(false);

    setScrollTop(480);
    act(() => div.dispatchEvent(new Event("scroll")));
    expect(result.current.sticky).toBe(false);

    setScrollTop(244);
    act(() => div.dispatchEvent(new Event("scroll")));

    expect(result.current.sticky).toBe(false);
    expect(result.current.stickyRef.current).toBe(false);
  });

  it("pauses following when PageUp scrolls the transcript from body focus", () => {
    const { ref } = makeScrollRef(1000, 500, 500);
    const { result } = renderHook(() => useStickyBottom(ref, 1));

    act(() =>
      document.body.dispatchEvent(
        new KeyboardEvent("keydown", { key: "PageUp", bubbles: true }),
      ),
    );

    expect(result.current.sticky).toBe(false);
  });

  it("does not treat PageUp in an editor as transcript navigation", () => {
    const { ref, div } = makeScrollRef(1000, 500, 500);
    const editor = document.createElement("textarea");
    div.appendChild(editor);
    const { result } = renderHook(() => useStickyBottom(ref, 1));

    act(() =>
      editor.dispatchEvent(
        new KeyboardEvent("keydown", { key: "PageUp", bubbles: true }),
      ),
    );

    expect(result.current.sticky).toBe(true);
  });

  it("does not treat PageUp in a dialog as transcript navigation", () => {
    const { ref } = makeScrollRef(1000, 500, 500);
    const dialog = document.createElement("div");
    dialog.setAttribute("role", "dialog");
    const cancelButton = document.createElement("button");
    dialog.appendChild(cancelButton);
    document.body.appendChild(dialog);
    const { result } = renderHook(() => useStickyBottom(ref, 1));

    act(() =>
      cancelButton.dispatchEvent(
        new KeyboardEvent("keydown", { key: "PageUp", bubbles: true }),
      ),
    );

    expect(result.current.sticky).toBe(true);
  });

  it("does not treat body PageUp as transcript navigation while a dialog is open", () => {
    const { ref } = makeScrollRef(1000, 500, 500);
    const dialog = document.createElement("div");
    dialog.setAttribute("role", "dialog");
    document.body.appendChild(dialog);
    const { result } = renderHook(() => useStickyBottom(ref, 1));

    act(() =>
      document.body.dispatchEvent(
        new KeyboardEvent("keydown", { key: "PageUp", bubbles: true }),
      ),
    );

    expect(result.current.sticky).toBe(true);
  });

  it("pauses following on an upward touch gesture", () => {
    const { ref, div } = makeScrollRef(1000, 500, 500);
    const { result } = renderHook(() => useStickyBottom(ref, 1));

    act(() => div.dispatchEvent(touchEvent("touchstart", 100)));
    act(() => div.dispatchEvent(touchEvent("touchmove", 140)));

    expect(result.current.sticky).toBe(false);
  });

  it("only treats scroll feedback as user input while the scrollbar is held", () => {
    const { ref, div, setScrollTop } = makeScrollRef(1000, 500, 500);
    Object.defineProperties(div, {
      clientWidth: { configurable: true, value: 480 },
      offsetWidth: { configurable: true, value: 500 },
    });
    div.getBoundingClientRect = () =>
      ({ right: 500 } as DOMRect);
    const { result } = renderHook(() => useStickyBottom(ref, 1));

    act(() => div.dispatchEvent(pointerEvent("pointerdown", 495)));
    act(() => window.dispatchEvent(pointerEvent("pointerup", 495)));
    setScrollTop(300);
    act(() => div.dispatchEvent(new Event("scroll")));
    expect(result.current.sticky).toBe(true);

    act(() => div.dispatchEvent(pointerEvent("pointerdown", 495)));
    act(() => div.dispatchEvent(new Event("scroll")));
    expect(result.current.sticky).toBe(false);

    setScrollTop(500);
    act(() => div.dispatchEvent(new Event("scroll")));
    expect(result.current.sticky).toBe(true);
  });

  it("re-arms after a short upward scroll is dragged back to the bottom", () => {
    const { ref, div, setScrollTop } = makeScrollRef(1000, 500, 500);
    Object.defineProperties(div, {
      clientWidth: { configurable: true, value: 480 },
      offsetWidth: { configurable: true, value: 500 },
    });
    div.getBoundingClientRect = () => ({ right: 500 } as DOMRect);
    const { result } = renderHook(() => useStickyBottom(ref, 1));

    act(() => div.dispatchEvent(new WheelEvent("wheel", { deltaY: -10 })));
    setScrollTop(490);
    act(() => div.dispatchEvent(new Event("scroll")));
    expect(result.current.sticky).toBe(false);

    act(() => div.dispatchEvent(pointerEvent("pointerdown", 495)));
    setScrollTop(500);
    act(() => div.dispatchEvent(new Event("scroll")));
    act(() => window.dispatchEvent(pointerEvent("pointerup", 495)));

    expect(result.current.sticky).toBe(true);
  });

  it("removes input listeners when unmounted", () => {
    const { ref, div } = makeScrollRef(1000, 500, 500);
    const { result, unmount } = renderHook(() => useStickyBottom(ref, 1));

    unmount();
    act(() => div.dispatchEvent(new WheelEvent("wheel", { deltaY: -40 })));

    expect(result.current.stickyRef.current).toBe(true);
  });

  it("unread grows as new turns arrive while away from bottom", () => {
    const { ref, div, setScrollTop } = makeScrollRef(1000, 500, 500);
    const { result, rerender } = renderHook(
      ({ n }) => useStickyBottom(ref, n),
      { initialProps: { n: 1 } },
    );
    act(() => div.dispatchEvent(new WheelEvent("wheel", { deltaY: -40 })));
    setScrollTop(0);
    act(() => div.dispatchEvent(new Event("scroll")));
    expect(result.current.sticky).toBe(false);
    rerender({ n: 3 });
    expect(result.current.unread).toBe(2);
  });

  it("scrolling back to bottom re-arms sticky and clears unread", () => {
    const { ref, div, setScrollTop } = makeScrollRef(1000, 500, 500);
    const { result, rerender } = renderHook(
      ({ n }) => useStickyBottom(ref, n),
      { initialProps: { n: 1 } },
    );
    act(() => div.dispatchEvent(new WheelEvent("wheel", { deltaY: -40 })));
    setScrollTop(0);
    act(() => div.dispatchEvent(new Event("scroll")));
    rerender({ n: 3 });
    expect(result.current.unread).toBe(2);
    setScrollTop(500);
    act(() => div.dispatchEvent(new Event("scroll")));
    expect(result.current.sticky).toBe(true);
    expect(result.current.unread).toBe(0);
  });

  it("jumpToBottom re-arms sticky and clears unread", () => {
    const { ref, div, setScrollTop } = makeScrollRef(1000, 500, 500);
    const { result, rerender } = renderHook(
      ({ n }) => useStickyBottom(ref, n),
      { initialProps: { n: 1 } },
    );
    act(() => div.dispatchEvent(new WheelEvent("wheel", { deltaY: -40 })));
    setScrollTop(0);
    act(() => div.dispatchEvent(new Event("scroll")));
    rerender({ n: 3 });
    expect(result.current.unread).toBe(2);
    act(() => result.current.jumpToBottom());
    expect(result.current.sticky).toBe(true);
    expect(result.current.unread).toBe(0);
  });

  it("re-arms following when the active session changes", () => {
    const { ref, div, setScrollTop } = makeScrollRef(1000, 500, 500);
    const { result, rerender } = renderHook(
      ({ sessionKey }) => useStickyBottom(ref, 1, sessionKey),
      { initialProps: { sessionKey: "session-a" } },
    );
    act(() => div.dispatchEvent(new WheelEvent("wheel", { deltaY: -40 })));
    setScrollTop(0);
    act(() => div.dispatchEvent(new Event("scroll")));
    expect(result.current.sticky).toBe(false);

    rerender({ sessionKey: "session-b" });

    expect(result.current.sticky).toBe(true);
    expect(result.current.stickyRef.current).toBe(true);
    expect(result.current.unread).toBe(0);

    rerender({ sessionKey: "session-a" });

    expect(result.current.sticky).toBe(true);
    expect(result.current.stickyRef.current).toBe(true);
    expect(result.current.unread).toBe(0);
  });
});
