import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

vi.mock("../api/agent", () => ({
  activateAgentSession: vi.fn().mockResolvedValue({ activeId: "s1" }),
  deleteAgentSession: vi.fn().mockResolvedValue({ closed: true }),
}));

import { MultiSessionBar } from "../components/cc/MultiSessionBar";
import {
  useCcStore,
  INITIAL_REDUCER_STATE,
  type PerSessionState,
} from "../stores/ccStore";
import { activateAgentSession as apiActivateSession, deleteAgentSession as apiDeleteSession } from "../api/agent";

/** Default `connected: true` (a live cc process) so the row renders; override
 * with `connected: false` to test the "not yet sent" filtering. */
function makeSession(over: Partial<PerSessionState> & { name?: string }): PerSessionState {
  return {
    ...INITIAL_REDUCER_STATE,
    workdir: "/wd",
    effort: null,
    name: "wd",
    revertEnabled: false,
    transportError: null,
    abort: null,
    connected: true,
    memoryEnabled: true,
    profileEnabled: true,
    runtime: "claude_code",
    nativeSessionId: null,
    permission: null,
    capabilities: ["tools", "approval", "checkpoint", "workflow"],
    lastSeq: null,
    needsReplay: false,
    ...over,
  };
}

function setSessions(
  sessions: Record<string, PerSessionState>,
  activeSid: string | null,
): void {
  useCcStore.setState({
    sessions,
    activeSid,
    history: [],
    historyTotal: 0,
    loadingHistory: false,
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  setSessions({}, null);
});

describe("MultiSessionBar (slice-028 v2: only live connections)", () => {
  it("renders the empty hint when there are no connections", () => {
    render(<MultiSessionBar onNewSameWorkdir={() => {}} onChangeWorkdir={() => {}} />);
    expect(screen.getByText(/暂无连接/)).toBeInTheDocument();
  });

  it("lists connected sessions; the active one is highlighted", () => {
    setSessions(
      {
        s1: makeSession({ name: "trowel-py" }),
        s2: makeSession({ name: "wiki" }),
      },
      "s1",
    );
    render(<MultiSessionBar onNewSameWorkdir={() => {}} onChangeWorkdir={() => {}} />);
    expect(screen.getByText("trowel-py")).toBeInTheDocument();
    expect(screen.getByText("wiki")).toBeInTheDocument();
    // active row carries the active class on the item container
    const activeItem = screen.getByText("trowel-py").closest(".cc-multibar__item");
    expect(activeItem?.className).toMatch(/--active/);
  });

  it("shows the M·P condition marker per session (slice-060)", () => {
    setSessions(
      { s1: makeSession({ name: "A", memoryEnabled: false, profileEnabled: true }) },
      "s1",
    );
    const { container } = render(
      <MultiSessionBar onNewSameWorkdir={() => {}} onChangeWorkdir={() => {}} />,
    );
    const cond = container.querySelector(".cc-multibar__cond");
    expect(cond).not.toBeNull();
    // M off (dim), P on (green) — so four experiment sessions stay distinguishable
    expect(cond?.querySelectorAll(".cc-multibar__cond-off")).toHaveLength(1);
    expect(cond?.querySelectorAll(".cc-multibar__cond-on")).toHaveLength(1);
  });

  it("does NOT render sessions that haven't sent a message (connected=false)", () => {
    // s1 is a live connection; s2 is a "+" / load-history state (no cc process yet)
    setSessions(
      {
        s1: makeSession({ name: "live" }),
        s2: makeSession({ name: "just-opened", connected: false }),
      },
      "s2",
    );
    render(<MultiSessionBar onNewSameWorkdir={() => {}} onChangeWorkdir={() => {}} />);
    expect(screen.getByText("live")).toBeInTheDocument();
    expect(screen.queryByText("just-opened")).toBeNull();
  });

  it("does NOT render exited sessions (they are dropped, not greyed)", () => {
    setSessions(
      {
        s1: makeSession({
          name: "gone",
          meta: { ...INITIAL_REDUCER_STATE.meta, exited: true, exitReturncode: 0 },
        }),
      },
      null,
    );
    render(<MultiSessionBar onNewSameWorkdir={() => {}} onChangeWorkdir={() => {}} />);
    expect(screen.queryByText("gone")).toBeNull();
    expect(screen.getByText(/暂无连接/)).toBeInTheDocument();
  });

  it("shows the running text for an in-turn session", () => {
    setSessions(
      { s1: makeSession({ name: "x", abort: new AbortController() }) },
      "s1",
    );
    render(<MultiSessionBar onNewSameWorkdir={() => {}} onChangeWorkdir={() => {}} />);
    expect(screen.getByText(/生成中/)).toBeInTheDocument();
  });

  it("clicking a row calls activateSession(sid)", async () => {
    setSessions(
      { s1: makeSession({ name: "a" }), s2: makeSession({ name: "b" }) },
      "s1",
    );
    render(<MultiSessionBar onNewSameWorkdir={() => {}} onChangeWorkdir={() => {}} />);
    fireEvent.click(screen.getByText("b"));
    // activate is async (awaits dropTempActive + apiActivateSession)
    await waitFor(() => {
      expect(apiActivateSession).toHaveBeenCalledWith("s2");
    });
  });

  it("× close button calls closeSession → DELETE", async () => {
    setSessions(
      { s1: makeSession({ name: "a" }) },
      "s1",
    );
    render(<MultiSessionBar onNewSameWorkdir={() => {}} onChangeWorkdir={() => {}} />);
    fireEvent.click(screen.getByLabelText("关闭 a"));
    await waitFor(() => {
      expect(apiDeleteSession).toHaveBeenCalledWith("s1");
    });
  });

  it("+ and ⇄ fire their callbacks", () => {
    const onNew = vi.fn();
    const onChange = vi.fn();
    render(<MultiSessionBar onNewSameWorkdir={onNew} onChangeWorkdir={onChange} />);
    fireEvent.click(screen.getByLabelText("同目录新开"));
    expect(onNew).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByLabelText("换目录新开"));
    expect(onChange).toHaveBeenCalledTimes(1);
  });

  it("+ is always enabled (cap is enforced on send, not on create)", () => {
    // even at the connected cap, + must stay enabled — it only prepares a new
    // chat (connected=false); the cap fires when the user actually sends.
    const sessions: Record<string, PerSessionState> = {};
    for (let i = 0; i < 20; i++) {
      sessions[`s${i}`] = makeSession({ name: `s${i}` });
    }
    setSessions(sessions, "s0");
    render(<MultiSessionBar onNewSameWorkdir={() => {}} onChangeWorkdir={() => {}} />);
    expect(screen.getByLabelText("同目录新开")).not.toBeDisabled();
  });

  it("connection cap counts only connected sessions (temp states don't count)", () => {
    const sessions: Record<string, PerSessionState> = {};
    for (let i = 0; i < 19; i++) {
      sessions[`s${i}`] = makeSession({ name: `s${i}` });
    }
    sessions["temp"] = makeSession({ name: "temp", connected: false });
    setSessions(sessions, "s0");
    render(<MultiSessionBar onNewSameWorkdir={() => {}} onChangeWorkdir={() => {}} />);
    expect(screen.getByText(/19\/20 连接/)).toBeInTheDocument();
  });

  it("footer shows running + connection counts", () => {
    setSessions(
      {
        s1: makeSession({ name: "a", abort: new AbortController() }),
        s2: makeSession({ name: "b" }),
      },
      "s1",
    );
    render(<MultiSessionBar onNewSameWorkdir={() => {}} onChangeWorkdir={() => {}} />);
    expect(screen.getByText(/1\/5 在跑/)).toBeInTheDocument();
    expect(screen.getByText(/2\/20 连接/)).toBeInTheDocument();
  });
});
