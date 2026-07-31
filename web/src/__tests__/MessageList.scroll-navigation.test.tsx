import { act, fireEvent, render, screen } from "@testing-library/react";
import { useRef } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { MessageList } from "../components/cc/MessageList";
import type { Turn } from "../stores/ccStore";

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
