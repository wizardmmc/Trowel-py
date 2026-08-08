import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { act } from "react";

import { SpinnerLine } from "../components/cc/SpinnerLine";
import { getExpectedRuntimePresentation } from "../agent/runtimes";
import {
  useAgentStore,
  INITIAL_REDUCER_STATE,
  type PerSessionState,
} from "../agent";

const SID = "s1";

function makeSession(over: Partial<PerSessionState> = {}): PerSessionState {
  return {
    ...INITIAL_REDUCER_STATE,
    workdir: "/wd",
    effort: null,
    name: "wd",
    displayTitle: "wd",
    titleSource: "native",
    checkpointAvailable: false,
    transportError: null,
    abort: null,
    connected: true,
    memoryEnabled: true,
    profileEnabled: true,
    runtime: "claude_code",
    nativeSessionId: null,
    permission: null,
    capabilities: getExpectedRuntimePresentation("claude_code")
      .expectedCapabilities,
    lastSeq: null,
    needsReplay: false,
    ...over,
    resourceState: over.resourceState ?? "connected",
    turnState: over.turnState ?? "idle",
    liveState: over.liveState ?? "ready",
    currentTurnId: over.currentTurnId ?? null,
    stateGeneration: over.stateGeneration ?? 1,
    codexSubagents: over.codexSubagents ?? {},
  };
}

function setActive(session: PerSessionState): void {
  useAgentStore.setState({
    sessions: { [SID]: session },
    activeSid: SID,
  });
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(10000);
  useAgentStore.setState({
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

describe("SpinnerLine", () => {
  it("renders nothing when phase is not thinking", () => {
    setActive(makeSession({ phase: "idle" }));
    render(<SpinnerLine />);
    expect(screen.queryByTestId("cc-spinner")).toBeNull();
  });

  it("renders nothing when there is no active session", () => {
    useAgentStore.setState({ activeSid: null, sessions: {} });
    render(<SpinnerLine />);
    expect(screen.queryByTestId("cc-spinner")).toBeNull();
  });

  it("renders the spinner with a verb while thinking", () => {
    setThinking({ startedAt: 10000 });
    render(<SpinnerLine />);
    const spinner = screen.getByTestId("cc-spinner");
    expect(spinner).toBeInTheDocument();
    expect(spinner.textContent).toMatch(/[A-Za-z]+…/);
  });

  it("hides seconds/tokens before 5s and shows them after", () => {
    setThinking({ startedAt: 10000, tokens: 5 });
    render(<SpinnerLine />);
    expect(screen.queryByText(/tokens/)).toBeNull();
    expect(screen.queryByText(/^\d+ 秒$/)).toBeNull();

    act(() => {
      vi.setSystemTime(16000);
      vi.advanceTimersByTime(200);
    });
    expect(screen.getByText(/^6 秒$/)).toBeInTheDocument();
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

  it("uses a Chinese effort summary when effort is set", () => {
    setThinking({ startedAt: 10000, effort: "high" });
    render(<SpinnerLine />);
    expect(screen.queryByText(/强度思考/)).toBeNull();
    act(() => {
      vi.setSystemTime(16000);
      vi.advanceTimersByTime(200);
    });
    expect(screen.getByText(/高强度思考/)).toBeInTheDocument();
  });

  it("uses a Chinese thinking summary without effort", () => {
    setThinking({ startedAt: 10000, effort: null });
    render(<SpinnerLine />);
    act(() => {
      vi.setSystemTime(16000);
      vi.advanceTimersByTime(200);
    });
    expect(document.querySelector(".cc-spinner__think")).toHaveTextContent(
      "思考中",
    );
  });
});
