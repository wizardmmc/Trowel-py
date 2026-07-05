import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { act } from "react";

import { SpinnerLine } from "../components/cc/SpinnerLine";
import {
  useCcStore,
  INITIAL_REDUCER_STATE,
  type PerSessionState,
} from "../stores/ccStore";

const SID = "s1";

/** Build a minimal active session record (slice-028 multi-session shape). */
function makeSession(over: Partial<PerSessionState> = {}): PerSessionState {
  return {
    ...INITIAL_REDUCER_STATE,
    workdir: "/wd",
    effort: null,
    name: "wd",
    revertEnabled: false,
    transportError: null,
    abort: null,
    connected: true,
    ...over,
  };
}

function setActive(session: PerSessionState): void {
  useCcStore.setState({
    sessions: { [SID]: session },
    activeSid: SID,
  });
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(10000);
  useCcStore.setState({
    sessions: {},
    activeSid: null,
    history: [],
    historyTotal: 0,
    loadingHistory: false,
  });
});

afterEach(() => {
  vi.useRealTimers();
});

function setThinking(over: {
  startedAt?: number;
  tokens?: number | null;
  effort?: string | null;
}) {
  const session = makeSession({
    phase: "thinking",
    effort: over.effort ?? null,
    meta: {
      ...INITIAL_REDUCER_STATE.meta,
      thinkingStartedAt: over.startedAt ?? 10000,
      thinkingTokens: over.tokens === undefined ? 5 : over.tokens,
    },
  });
  setActive(session);
}

describe("SpinnerLine (slice-025-a A1)", () => {
  it("renders nothing when phase is not thinking", () => {
    setActive(makeSession({ phase: "idle" }));
    render(<SpinnerLine />);
    expect(screen.queryByTestId("cc-spinner")).toBeNull();
  });

  it("renders nothing when there is no active session", () => {
    useCcStore.setState({ activeSid: null, sessions: {} });
    render(<SpinnerLine />);
    expect(screen.queryByTestId("cc-spinner")).toBeNull();
  });

  it("renders the spinner with a verb while thinking", () => {
    setThinking({ startedAt: 10000 });
    render(<SpinnerLine />);
    const spinner = screen.getByTestId("cc-spinner");
    expect(spinner).toBeInTheDocument();
    // a verb followed by an ellipsis
    expect(spinner.textContent).toMatch(/[A-Za-z]+…/);
  });

  it("hides seconds/tokens before 5s and shows them after", () => {
    setThinking({ startedAt: 10000, tokens: 5 });
    render(<SpinnerLine />);
    // now == startedAt -> 0s elapsed, stats hidden
    expect(screen.queryByText(/tokens/)).toBeNull();
    expect(screen.queryByText(/^\d+s$/)).toBeNull();

    // advance to 6s and tick the interval
    act(() => {
      vi.setSystemTime(16000);
      vi.advanceTimersByTime(200);
    });
    expect(screen.getByText(/^6s$/)).toBeInTheDocument();
    expect(screen.getByText(/↓ 5 tokens/)).toBeInTheDocument();
  });

  it("does not show tokens when thinkingTokens is null", () => {
    setThinking({ startedAt: 10000, tokens: null });
    render(<SpinnerLine />);
    act(() => {
      vi.setSystemTime(16000);
      vi.advanceTimersByTime(200);
    });
    expect(screen.queryByText(/tokens/)).toBeNull();
  });

  it("shows 'thinking with <effort> effort' only when effort is set", () => {
    setThinking({ startedAt: 10000, effort: "high" });
    render(<SpinnerLine />);
    // before 5s, the effort text is part of the hidden stats block
    expect(screen.queryByText(/effort/)).toBeNull();
    act(() => {
      vi.setSystemTime(16000);
      vi.advanceTimersByTime(200);
    });
    expect(screen.getByText(/thinking with high effort/)).toBeInTheDocument();
  });

  it("without effort, shows bare 'thinking' after 5s", () => {
    setThinking({ startedAt: 10000, effort: null });
    render(<SpinnerLine />);
    act(() => {
      vi.setSystemTime(16000);
      vi.advanceTimersByTime(200);
    });
    // bare thinking, no 'with ... effort'
    expect(screen.getByText(/thinking/).textContent).not.toMatch(/with/);
  });
});
