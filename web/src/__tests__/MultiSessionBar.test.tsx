import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

vi.mock("../agent/transport/api", () => ({
  activateAgentSession: vi.fn().mockResolvedValue({ activeId: "s1" }),
  deleteAgentSession: vi.fn().mockResolvedValue({ closed: true }),
  renameAgentSessionTitle: vi.fn().mockImplementation((_sid, title) =>
    Promise.resolve({ display_title: title, title_source: "manual" }),
  ),
  listAgentRequests: vi.fn().mockResolvedValue([]),
}));

import { MultiSessionBar } from "../components/cc/MultiSessionBar";
import { getExpectedRuntimePresentation } from "../agent/runtimes";
import {
  useAgentStore,
  INITIAL_REDUCER_STATE,
  type PerSessionState,
} from "../agent";
import { activateAgentSession as apiActivateSession, deleteAgentSession as apiDeleteSession } from "../agent/transport";
import { renameAgentSessionTitle as apiRenameSessionTitle } from "../agent/transport";

function makeSession(over: Partial<PerSessionState> & { name?: string }): PerSessionState {
  return {
    ...INITIAL_REDUCER_STATE,
    workdir: "/wd",
    effort: null,
    name: "wd",
    displayTitle: over.displayTitle ?? over.name ?? "wd",
    titleSource: over.titleSource ?? "generated",
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
    codexSubagents: over.codexSubagents ?? {},
  };
}

function setSessions(
  sessions: Record<string, PerSessionState>,
  activeSid: string | null,
): void {
  useAgentStore.setState({
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

describe("MultiSessionBar", () => {
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
    const activeItem = screen.getByText("trowel-py").closest(".cc-multibar__item");
    expect(activeItem?.className).toMatch(/--active/);
  });

  it("groups sessions by full workdir and keeps creation order when active changes", () => {
    setSessions(
      {
        s1: makeSession({
          workdir: "/workspace/alpha",
          name: "alpha",
          displayTitle: "第一个任务",
        }),
        s2: makeSession({
          workdir: "/workspace/alpha",
          name: "alpha #2",
          displayTitle: "第二个任务",
        }),
        s3: makeSession({
          workdir: "/other/alpha",
          name: "alpha",
          displayTitle: "第三个任务",
        }),
      },
      "s2",
    );

    render(<MultiSessionBar onNewSameWorkdir={() => {}} onChangeWorkdir={() => {}} />);

    const groups = screen.getAllByRole("group");
    expect(groups).toHaveLength(2);
    expect(groups[0]).toHaveAccessibleName("alpha");
    expect(groups[1]).toHaveAccessibleName("alpha");
    const titles = screen.getAllByTestId("session-title").map((node) => node.textContent);
    expect(titles).toEqual(["第一个任务", "第二个任务", "第三个任务"]);
  });

  it("shows a semantic title instead of the temporary numbered name", () => {
    setSessions(
      {
        s1: makeSession({
          name: "trowel-py #2",
          displayTitle: "按目录聚合会话",
        }),
      },
      "s1",
    );

    render(<MultiSessionBar onNewSameWorkdir={() => {}} onChangeWorkdir={() => {}} />);

    expect(screen.getByText("按目录聚合会话")).toBeInTheDocument();
    expect(screen.queryByText("trowel-py #2")).toBeNull();
  });

  it("shows the M·P condition marker per session", () => {
    setSessions(
      { s1: makeSession({ name: "A", memoryEnabled: false, profileEnabled: true }) },
      "s1",
    );
    const { container } = render(
      <MultiSessionBar onNewSameWorkdir={() => {}} onChangeWorkdir={() => {}} />,
    );
    const cond = container.querySelector(".cc-multibar__cond");
    expect(cond).not.toBeNull();
    expect(cond?.querySelectorAll(".cc-multibar__cond-off")).toHaveLength(1);
    expect(cond?.querySelectorAll(".cc-multibar__cond-on")).toHaveLength(1);
  });

  it("does NOT render sessions that haven't sent a message (connected=false)", () => {
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

  it("uses the full Claude name and Chinese idle state", () => {
    setSessions({ s1: makeSession({ name: "claude" }) }, "s1");
    render(<MultiSessionBar onNewSameWorkdir={() => {}} onChangeWorkdir={() => {}} />);

    expect(screen.getByText("Claude")).toBeInTheDocument();
    expect(screen.getByText(/空闲/)).toBeInTheDocument();
    expect(screen.queryByText(/idle/)).toBeNull();
  });

  it("shows background waiting instead of generating while a task is pending", () => {
    setSessions(
      {
        s1: makeSession({
          name: "x",
          abort: new AbortController(),
          phase: "background_waiting",
        }),
      },
      "s1",
    );
    render(<MultiSessionBar onNewSameWorkdir={() => {}} onChangeWorkdir={() => {}} />);
    expect(screen.getByText(/等后台任务/)).toBeInTheDocument();
    expect(screen.queryByText(/生成中/)).toBeNull();
  });

  it("clicking a row switches the renderer-local active session", async () => {
    setSessions(
      { s1: makeSession({ name: "a" }), s2: makeSession({ name: "b" }) },
      "s1",
    );
    render(<MultiSessionBar onNewSameWorkdir={() => {}} onChangeWorkdir={() => {}} />);
    fireEvent.click(screen.getByText("b"));
    await waitFor(() => expect(useAgentStore.getState().activeSid).toBe("s2"));
    expect(apiActivateSession).not.toHaveBeenCalled();
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

  it("renames a session inline", async () => {
    setSessions(
      {
        s1: makeSession({
          displayTitle: "旧标题",
          titleSource: "generated",
        }),
      },
      "s1",
    );
    render(<MultiSessionBar onNewSameWorkdir={() => {}} onChangeWorkdir={() => {}} />);

    fireEvent.click(screen.getByLabelText("重命名 旧标题"));
    const input = screen.getByRole("textbox", { name: "会话标题" });
    fireEvent.change(input, { target: { value: "新标题" } });
    fireEvent.submit(input.closest("form")!);

    await waitFor(() => {
      expect(apiRenameSessionTitle).toHaveBeenCalledWith("s1", "新标题");
      expect(screen.getByText("新标题")).toBeInTheDocument();
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

  it("hides delegate sessions from rows and user counts", () => {
    setSessions(
      {
        user: makeSession({ name: "user" }),
        delegate: makeSession({
          name: "delegate",
          sessionKind: "delegate",
          abort: new AbortController(),
        }),
      },
      "user",
    );

    render(<MultiSessionBar onNewSameWorkdir={() => {}} onChangeWorkdir={() => {}} />);

    expect(screen.getByText("user")).toBeInTheDocument();
    expect(screen.queryByText("delegate")).toBeNull();
    expect(screen.getByText(/0\/5 在跑/)).toBeInTheDocument();
    expect(screen.getByText(/1\/20 连接/)).toBeInTheDocument();
  });
});
