import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useAgentStoreFrameSelector } from "../agent/application";
import { useAgentStore } from "../agent";

describe("useAgentStoreFrameSelector", () => {
  const frames: FrameRequestCallback[] = [];

  beforeEach(() => {
    frames.length = 0;
    vi.spyOn(window, "requestAnimationFrame").mockImplementation((callback) => {
      frames.push(callback);
      return frames.length;
    });
    useAgentStore.setState((state) => ({ ...state, historyTotal: 0 }));
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("publishes many store updates once with the latest value on the next frame", () => {
    const renders: number[] = [];
    const { result } = renderHook(() => {
      const value = useAgentStoreFrameSelector((state) => state.historyTotal);
      renders.push(value);
      return value;
    });

    act(() => {
      useAgentStore.setState((state) => ({ ...state, historyTotal: 1 }));
      useAgentStore.setState((state) => ({ ...state, historyTotal: 2 }));
      useAgentStore.setState((state) => ({ ...state, historyTotal: 3 }));
    });

    expect(result.current).toBe(0);
    expect(frames).toHaveLength(1);

    act(() => frames.shift()?.(16));

    expect(result.current).toBe(3);
    expect(renders).toEqual([0, 3]);
  });

  it("does not schedule a frame when the selected value is unchanged", () => {
    renderHook(() =>
      useAgentStoreFrameSelector((state) => state.historyTotal),
    );

    act(() => {
      useAgentStore.setState((state) => ({
        ...state,
        historyError: "background change",
      }));
    });

    expect(frames).toHaveLength(0);
  });
});
