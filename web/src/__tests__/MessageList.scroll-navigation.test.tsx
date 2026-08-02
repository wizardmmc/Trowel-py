import { act, fireEvent, render, screen } from "@testing-library/react";
import { StrictMode, useRef } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { MessageList } from "../agent/ui";
import { useStickyBottom } from "../components/cc/useStickyBottom";
import type { Turn } from "../agent";

function makeTurn(index: number, text = `回答 ${index}`): Turn {
  return {
    id: `turn-${index}`,
    userText: `问题 ${index}`,
    items: [{ kind: "text", text }],
    status: "done",
    turnId: null,
    revertible: false,
  };
}

function rect(top: number, bottom: number): DOMRect {
  return {
    top,
    bottom,
    left: 0,
    right: 600,
    width: 600,
    height: bottom - top,
    x: 0,
    y: top,
    toJSON: () => ({}),
  };
}

function ScrollHarness({
  turns,
  following = true,
}: {
  readonly turns: readonly Turn[];
  readonly following?: boolean;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  return (
    <div ref={scrollRef} data-testid="scroll-pane">
      <MessageList
        turns={turns}
        streaming={false}
        scrollRef={scrollRef}
        sticky={following}
      />
    </div>
  );
}

function StickyScrollHarness({ turns }: { readonly turns: readonly Turn[] }) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const { sticky, stickyRef, pauseFollowing } = useStickyBottom(
    scrollRef,
    turns.length,
    "sticky-repro",
  );
  return (
    <>
      <output data-testid="following-state">
        {sticky ? "following" : "paused"}
      </output>
      <div ref={scrollRef} data-testid="sticky-scroll-pane">
        <MessageList
          turns={turns}
          streaming
          scrollRef={scrollRef}
          sticky={sticky}
          followingRef={stickyRef}
          onLeaveBottom={pauseFollowing}
        />
      </div>
    </>
  );
}

function installGrowingScrollMetrics(
  pane: HTMLElement,
  initial: {
    readonly scrollHeight: number;
    readonly clientHeight: number;
    readonly scrollTop: number;
  },
) {
  let scrollHeight = initial.scrollHeight;
  let scrollTop = initial.scrollTop;
  Object.defineProperties(pane, {
    clientHeight: { configurable: true, get: () => initial.clientHeight },
    scrollHeight: { configurable: true, get: () => scrollHeight },
    scrollTop: {
      configurable: true,
      get: () => scrollTop,
      set: (value: number) => {
        scrollTop = value;
      },
    },
  });
  pane.scrollTo = ((options: ScrollToOptions) => {
    const requested = Number(options.top ?? scrollTop);
    scrollTop = Math.min(
      Math.max(0, requested),
      Math.max(0, scrollHeight - initial.clientHeight),
    );
  }) as typeof pane.scrollTo;
  return {
    getScrollTop: () => scrollTop,
    setScrollTop: (value: number) => {
      scrollTop = value;
    },
    setScrollHeight: (value: number) => {
      scrollHeight = value;
    },
  };
}

function installScrollMetrics(pane: HTMLElement) {
  let scrollTop = 0;
  Object.defineProperties(pane, {
    clientHeight: { configurable: true, get: () => 300 },
    scrollHeight: {
      configurable: true,
      get: () => pane.querySelectorAll(".cc-turn").length * 100,
    },
    scrollTop: {
      configurable: true,
      get: () => scrollTop,
      set: (value: number) => {
        scrollTop = value;
      },
    },
  });
  pane.scrollTo = ((options: ScrollToOptions) => {
    scrollTop = Number(options.top ?? scrollTop);
  }) as typeof pane.scrollTo;
  return {
    getScrollTop: () => scrollTop,
    setScrollTop: (value: number) => {
      scrollTop = value;
    },
  };
}

describe("MessageList scroll window", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("mounts only the latest two turns, then prepends five without moving the reading anchor", () => {
    const turns = Array.from({ length: 8 }, (_, index) => makeTurn(index));
    render(<ScrollHarness turns={turns} />);
    const pane = screen.getByTestId("scroll-pane");
    const metrics = installScrollMetrics(pane);

    expect(screen.queryByText("问题 5")).toBeNull();
    expect(screen.getByText("问题 6")).toBeInTheDocument();
    expect(screen.getByText("问题 7")).toBeInTheDocument();
    expect(pane.querySelectorAll(".cc-turn")).toHaveLength(2);

    metrics.setScrollTop(0);
    act(() => pane.dispatchEvent(new Event("scroll")));

    expect(screen.getByText("问题 1")).toBeInTheDocument();
    expect(pane.querySelectorAll(".cc-turn")).toHaveLength(7);
    expect(metrics.getScrollTop()).toBe(500);
  });

  it("follows content growth on the next frame but preserves scrollTop while reading", () => {
    const frames: FrameRequestCallback[] = [];
    vi.spyOn(window, "requestAnimationFrame").mockImplementation((callback) => {
      frames.push(callback);
      return frames.length;
    });
    const turns = [makeTurn(0)];
    const { rerender } = render(<ScrollHarness turns={turns} />);
    const pane = screen.getByTestId("scroll-pane");
    const metrics = installScrollMetrics(pane);

    metrics.setScrollTop(12);
    rerender(
      <ScrollHarness
        turns={[makeTurn(0, "回答增长后的最新内容")]}
        following
      />,
    );
    expect(metrics.getScrollTop()).toBe(12);
    act(() => frames.shift()?.(16));
    expect(metrics.getScrollTop()).toBe(100);

    metrics.setScrollTop(37);
    rerender(
      <ScrollHarness
        turns={[makeTurn(0, "阅读态到达的新内容")]}
        following={false}
      />,
    );
    expect(metrics.getScrollTop()).toBe(37);
  });

  it("coalesces repeated sticky content growth into one scroll per frame", () => {
    const frames: FrameRequestCallback[] = [];
    vi.spyOn(window, "requestAnimationFrame").mockImplementation((callback) => {
      frames.push(callback);
      return frames.length;
    });
    const turns = [makeTurn(0)];
    const { rerender } = render(<ScrollHarness turns={turns} />);
    const pane = screen.getByTestId("scroll-pane");
    const metrics = installScrollMetrics(pane);
    const scrollTo = vi.spyOn(pane, "scrollTo");
    scrollTo.mockClear();

    rerender(<ScrollHarness turns={[makeTurn(0, "first growth")]} />);
    rerender(<ScrollHarness turns={[makeTurn(0, "second growth")]} />);

    expect(scrollTo).not.toHaveBeenCalled();
    expect(frames).toHaveLength(1);

    act(() => frames.shift()?.(16));

    expect(scrollTo).toHaveBeenCalledTimes(1);
    expect(metrics.getScrollTop()).toBe(100);
  });

  it("schedules bottom follow after StrictMode replays mount effects", () => {
    const frames = new Map<number, FrameRequestCallback>();
    let nextFrameId = 0;
    vi.spyOn(window, "requestAnimationFrame").mockImplementation((callback) => {
      nextFrameId += 1;
      frames.set(nextFrameId, callback);
      return nextFrameId;
    });
    vi.spyOn(window, "cancelAnimationFrame").mockImplementation((id) => {
      frames.delete(id);
    });
    const turns = [makeTurn(0)];
    const { rerender } = render(
      <StrictMode>
        <ScrollHarness turns={turns} />
      </StrictMode>,
    );
    const pane = screen.getByTestId("scroll-pane");
    installScrollMetrics(pane);
    const scrollTo = vi.spyOn(pane, "scrollTo");

    rerender(
      <StrictMode>
        <ScrollHarness turns={[makeTurn(0, "streamed growth")]} />
      </StrictMode>,
    );
    expect(frames).toHaveLength(1);
    act(() => frames.values().next().value?.(16));

    expect(scrollTo).toHaveBeenCalledTimes(1);
  });

  it("keeps following when a prior bottom scroll reports after more content grows", () => {
    const frames: FrameRequestCallback[] = [];
    vi.spyOn(window, "requestAnimationFrame").mockImplementation((callback) => {
      frames.push(callback);
      return frames.length;
    });
    const { rerender } = render(
      <StickyScrollHarness turns={[makeTurn(0, "initial content")]} />,
    );
    const pane = screen.getByTestId("sticky-scroll-pane");
    const metrics = installGrowingScrollMetrics(pane, {
      scrollHeight: 1025,
      clientHeight: 730,
      scrollTop: 295,
    });

    act(() => frames.shift()?.(0));
    metrics.setScrollHeight(1094);
    rerender(
      <StickyScrollHarness turns={[makeTurn(0, "first streamed growth")]} />,
    );
    act(() => frames.shift()?.(16));
    expect(metrics.getScrollTop()).toBe(364);

    metrics.setScrollHeight(1253);
    rerender(
      <StickyScrollHarness turns={[makeTurn(0, "second streamed growth")]} />,
    );
    act(() => pane.dispatchEvent(new Event("scroll")));

    expect(screen.getByTestId("following-state")).toHaveTextContent("following");
  });

  it("does not run a queued bottom follow after the user starts reading", () => {
    const frames: FrameRequestCallback[] = [];
    vi.spyOn(window, "requestAnimationFrame").mockImplementation((callback) => {
      frames.push(callback);
      return frames.length;
    });
    render(<StickyScrollHarness turns={[makeTurn(0, "streamed content")]} />);
    const pane = screen.getByTestId("sticky-scroll-pane");
    const metrics = installGrowingScrollMetrics(pane, {
      scrollHeight: 1000,
      clientHeight: 500,
      scrollTop: 500,
    });

    act(() => pane.dispatchEvent(new WheelEvent("wheel", { deltaY: -80 })));
    metrics.setScrollTop(360);

    expect(screen.getByTestId("following-state")).toHaveTextContent("paused");
    act(() => frames.shift()?.(16));
    expect(metrics.getScrollTop()).toBe(360);
  });

  it("loads older turns from an upward wheel gesture when the latest window cannot scroll", () => {
    const turns = Array.from({ length: 8 }, (_, index) => makeTurn(index));
    render(<ScrollHarness turns={turns} />);
    const pane = screen.getByTestId("scroll-pane");
    installScrollMetrics(pane);

    fireEvent.wheel(pane, { deltaY: -80 });

    expect(pane.querySelectorAll(".cc-turn")).toHaveLength(7);
    expect(screen.getByText("问题 1")).toBeInTheDocument();
  });
});

describe("MessageList current turn context", () => {
  it("hands context from the previous turn to the visible user card, then pins the new turn", () => {
    render(<ScrollHarness turns={[makeTurn(0), makeTurn(1)]} following={false} />);
    const pane = screen.getByTestId("scroll-pane");
    installScrollMetrics(pane);
    pane.getBoundingClientRect = () => rect(100, 500);

    const turn0 = pane.querySelector('[data-turn-index="0"]') as HTMLElement;
    const turn1 = pane.querySelector('[data-turn-index="1"]') as HTMLElement;
    const user0 = turn0.querySelector(".cc-msg--user") as HTMLElement;
    const user1 = turn1.querySelector(".cc-msg--user") as HTMLElement;

    turn0.getBoundingClientRect = () => rect(0, 90);
    user0.getBoundingClientRect = () => rect(0, 90);
    turn1.getBoundingClientRect = () => rect(168, 420);
    user1.getBoundingClientRect = () => rect(168, 230);
    fireEvent.scroll(pane);
    expect(screen.getByRole("button", { name: /回到问题 0/ })).toBeVisible();

    turn1.getBoundingClientRect = () => rect(132, 384);
    user1.getBoundingClientRect = () => rect(132, 194);
    fireEvent.scroll(pane);
    expect(screen.queryByRole("button", { name: /回到问题 1/ })).toBeNull();

    turn1.getBoundingClientRect = () => rect(20, 272);
    user1.getBoundingClientRect = () => rect(20, 82);
    fireEvent.scroll(pane);
    expect(screen.getByRole("button", { name: /回到问题 1/ })).toBeVisible();
  });

  it("jumps from the pinned context to the source user card", () => {
    render(<ScrollHarness turns={[makeTurn(0)]} following={false} />);
    const pane = screen.getByTestId("scroll-pane");
    const metrics = installScrollMetrics(pane);
    pane.getBoundingClientRect = () => rect(100, 500);
    const turn = pane.querySelector('[data-turn-index="0"]') as HTMLElement;
    const user = turn.querySelector(".cc-msg--user") as HTMLElement;
    metrics.setScrollTop(400);
    turn.getBoundingClientRect = () => rect(20, 272);
    user.getBoundingClientRect = () => rect(20, 82);
    fireEvent.scroll(pane);

    fireEvent.click(screen.getByRole("button", { name: /回到问题 0/ }));

    expect(metrics.getScrollTop()).toBe(312);
  });
});
