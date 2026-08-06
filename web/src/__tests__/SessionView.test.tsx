import { describe, it, expect, beforeEach, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

const { CC_CAPABILITIES, CODEX_CAPABILITIES } = vi.hoisted(() => ({
  CC_CAPABILITIES: [
    "tools", "models", "effort", "permission", "question", "interrupt",
    "slash_commands", "workflow", "tasks", "subagents", "checkpoint",
    "revert", "mcp",
  ] as const,
  CODEX_CAPABILITIES: [
    "tools", "models", "effort", "permission", "sandbox", "network_access",
    "approval", "interrupt", "slash_commands", "goal", "plan", "review",
    "subagents", "turn_diff", "mcp",
  ] as const,
}));

vi.mock("../agent/transport/api", () => ({
  createAgentSession: vi.fn().mockResolvedValue({
    session_id: "s1",
    runtime: "claude_code",
    native_session_id: null,
    workdir: "/wd",
    model: "glm-5.2",
    effort: null,
    permission: null,
    memory_enabled: true,
    profile_enabled: true,
    capabilities: CC_CAPABILITIES,
    name: "wd",
    connected: false,
    running: false,
  }),
  activateAgentSession: vi.fn().mockResolvedValue({ activeId: "s1" }),
  deleteAgentSession: vi.fn().mockResolvedValue({
    closed: true,
    status: "closed",
    remaining_resource_count: 0,
    remaining_resource_kinds: [],
    error: null,
  }),
  listAgentHistory: vi.fn().mockResolvedValue({ rows: [], nextCursor: null }),
  listActiveAgentSessions: vi.fn().mockResolvedValue({ sessions: [], activeId: null }),
  getAgentSessionDefaults: vi.fn().mockResolvedValue(null),
  listAgentRuntimes: vi.fn().mockResolvedValue([]),
  listAgentConnectionOptions: vi.fn().mockResolvedValue([
    {
      id: "claude-a",
      name: "Claude A",
      runtime: "claude_code",
      kind: "claude_compatible",
      identity_version: 1,
      available: true,
      disabled_reason: null,
      last_session_choice: { model: "glm-5.2", effort: null },
      models: [{
        id: "glm-5.2",
        display_name: "GLM 5.2",
        available: true,
        disabled_reason: null,
        efforts: [],
        default_effort: null,
      }],
    },
    {
      id: "codex-a",
      name: "Codex A",
      runtime: "codex",
      kind: "codex_custom",
      identity_version: 1,
      available: true,
      disabled_reason: null,
      last_session_choice: { model: "deepseek-v4-flash", effort: "high" },
      models: [{
        id: "deepseek-v4-flash",
        display_name: "DeepSeek V4 Flash",
        available: true,
        disabled_reason: null,
        efforts: ["low", "medium", "high", "xhigh"],
        default_effort: "high",
      }],
    },
  ]),
  listAgentModels: vi.fn().mockResolvedValue([]),
  listCodexCommands: vi.fn().mockResolvedValue([]),
  compactCodexSession: vi.fn().mockResolvedValue({ started: true }),
  startCodexReview: vi.fn().mockResolvedValue({
    reviewThreadId: "thread-1",
    turnId: "review-turn-1",
  }),
  listAgentRequests: vi.fn().mockResolvedValue([]),
  getCodexGoal: vi.fn().mockResolvedValue(null),
  getCodexSubagentHistory: vi.fn().mockResolvedValue([]),
  setCodexGoal: vi.fn(),
  clearCodexGoal: vi.fn().mockResolvedValue({ cleared: true }),
  startCodexTurn: vi.fn().mockResolvedValue({ turnId: "turn-1" }),
  startAgentTurn: vi.fn().mockResolvedValue({ turnId: "turn-1" }),
  updateAgentSessionSettings: vi.fn(),
  interruptAgentSession: vi.fn().mockResolvedValue({ interrupted: true }),
  answerAgentRequest: vi.fn(),
  agentMessagesUrl: (sid: string) => `/api/agent/sessions/${sid}/messages`,
  agentEventsUrl: () => "/api/agent/events",
}));

vi.mock("../agent/transport/stream", () => ({
  postMessageStream: vi.fn(async () => {}),
  getEventStream: vi.fn(
    (
      _url: string,
      _apply: unknown,
      options?: { onOpen?: (generation: string | null) => void },
    ) => {
      options?.onOpen?.("generation-1");
      return new Promise<void>(() => {});
    },
  ),
}));

vi.mock("../api/cc", () => ({
  listModels: vi.fn().mockResolvedValue([]),
  listSlashItems: vi.fn().mockResolvedValue([]),
  getHistory: vi.fn().mockResolvedValue([]),
  revertSession: vi.fn().mockResolvedValue({ reverted_turn_id: "x" }),
  answerElicit: vi.fn().mockResolvedValue({ ok: true }),
}));

import { SessionView } from "../agent/ui";
import { useAgentStore } from "../agent";
import { createNewSessionState } from "../agent/application/store/sessionState";
import { reduceAgentEvent } from "../agent/application/store/eventState";
import type { AgentEvent } from "../agent/transport";
import {
  createAgentSession as createSession,
  getAgentSessionDefaults,
  listAgentHistory as listSessions,
  listActiveAgentSessions as listActiveSessions,
  listAgentRuntimes,
  listAgentConnectionOptions,
  listAgentModels,
  listCodexCommands,
  compactCodexSession,
  getCodexSubagentHistory,
} from "../agent/transport";
import {
  loadNewSessionPreferences,
  saveNewSessionPreferences,
} from "../components/cc/newSessionPreferences";
import { listSlashItems } from "../api/cc";

/** 创建由测试控制完成顺序的 Promise。 */
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(getAgentSessionDefaults).mockResolvedValue(null);
  vi.mocked(listActiveSessions).mockResolvedValue({ sessions: [], activeId: null });
  vi.mocked(listSessions).mockResolvedValue({ rows: [], nextCursor: null });
  localStorage.clear();
  useAgentStore.setState({
    sessions: {},
    activeSid: null,
    history: [],
    historyTotal: 0,
    loadingHistory: false,
    loadingMoreHistory: false,
    historyCursor: null,
    historyHasMore: false,
    historyWorkdir: null,
  });
});

describe("SessionView", () => {
  it("does not request workdir-bound slash items on the Start page", () => {
    render(<SessionView workdir="" emptyWorkspaceContent={<div>Start</div>} />);

    expect(vi.mocked(listSlashItems)).not.toHaveBeenCalled();
  });

  it("does not start the legacy Codex manager when the Agent page opens", () => {
    render(<SessionView workdir="/wd" />);

    expect(vi.mocked(listAgentModels)).not.toHaveBeenCalled();
  });

  it("opens a Codex child timeline without a composer and returns to the parent", async () => {
    installCodexSession();
    let current = useAgentStore.getState().sessions.s1;
    const apply = (event: AgentEvent) => {
      const result = reduceAgentEvent(current, event);
      if (result.kind === "updated") current = result.session;
    };
    apply({
      schema: "agent-event-v1",
      session_id: "s1",
      runtime: "codex",
      seq: 1,
      type: "user",
      thread_id: "thread-1",
      turn_id: "parent-turn-1",
      item_id: null,
      payload: { text: "delegate" },
    });
    apply({
      schema: "agent-event-v1",
      session_id: "s1",
      runtime: "codex",
      seq: 2,
      type: "subagent_activity",
      thread_id: "thread-1",
      turn_id: "parent-turn-1",
      item_id: "activity-1",
      payload: {
        source: "subagent_activity",
        kind: "started",
        agent_thread_id: "child-thread-1",
        agent_path: "/root/probe",
      },
    });
    useAgentStore.setState({ sessions: { s1: current }, activeSid: "s1" });
    vi.mocked(getCodexSubagentHistory).mockResolvedValueOnce([
      {
        schema: "agent-event-v1",
        session_id: "s1",
        runtime: "codex",
        seq: 1,
        type: "turn_start",
        thread_id: "child-thread-1",
        turn_id: "child-turn-1",
        item_id: null,
        payload: { autonomous: true, revertible: false },
      },
      {
        schema: "agent-event-v1",
        session_id: "s1",
        runtime: "codex",
        seq: 2,
        type: "text",
        thread_id: "child-thread-1",
        turn_id: "child-turn-1",
        item_id: "message-1",
        payload: { text: "child result" },
      },
    ]);

    render(<SessionView workdir="/wd" />);
    fireEvent.click(screen.getByRole("button", { name: /root\/probe/ }));

    expect(await screen.findByText("child result")).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Subagent 路径" })).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "wd" }));
    expect(screen.getAllByText("delegate").length).toBeGreaterThan(0);
  });

  function installCodexSession() {
    const liveSession = codexLiveSession();
    vi.mocked(listActiveSessions).mockResolvedValue({
      sessions: [liveSession],
      activeId: "s1",
    });
    useAgentStore.setState({
      sessions: {
        s1: createNewSessionState(
          liveSession,
          { workdir: "/wd", runtime: "codex", effort: "high" },
        ),
      },
      activeSid: "s1",
    });
  }

  /** 返回与后端 live session 列表一致的 Codex 测试记录。 */
  function codexLiveSession() {
    return {
      session_id: "s1",
      runtime: "codex" as const,
      native_session_id: "thread-1",
      workdir: "/wd",
      model: "gpt-5.6-sol",
      effort: "high",
      permission: "Workspace write · on-request",
      memory_enabled: true,
      profile_enabled: true,
      capabilities: CODEX_CAPABILITIES,
      name: "wd",
      connected: true,
      running: false,
    };
  }

  it("mounts the three-column shell — multi-bar, center, todo-bar all present", async () => {
    const { container } = render(
      <SessionView workdir="/wd" onRequestChangeWorkdir={() => {}} />,
    );
    expect(container.querySelector(".cc-3col")).not.toBeNull();
    expect(container.querySelector(".cc-multibar")).not.toBeNull();
    expect(container.querySelector(".cc-todobar")).not.toBeNull();
    expect(container.querySelector(".cc-view")).not.toBeNull();
  });

  it("shows the multi-bar empty hint before any connection exists", () => {
    render(<SessionView workdir="/wd" />);
    expect(screen.getByText(/暂无连接/)).toBeInTheDocument();
  });

  it("loads the Codex roster and opens /status locally", async () => {
    installCodexSession();
    vi.mocked(listCodexCommands).mockResolvedValueOnce([
      {
        name: "status",
        description: "查看会话状态",
        source: "codex",
        action: "status",
        available_while_running: true,
      },
    ]);
    render(<SessionView workdir="/wd" />);

    const input = screen.getByLabelText("Agent 消息输入");
    fireEvent.change(input, { target: { value: "/status" } });
    fireEvent.click(await screen.findByRole("option", { name: /\/status/ }));

    expect(screen.getByRole("dialog", { name: "Codex 会话状态" })).toBeInTheDocument();
    expect(useAgentStore.getState().sessions.s1.turns).toHaveLength(0);
  });

  it("routes /compact to its command API and keeps it out of turns", async () => {
    installCodexSession();
    vi.mocked(listCodexCommands).mockResolvedValueOnce([
      {
        name: "compact",
        description: "压缩上下文",
        source: "codex",
        action: "compact",
        available_while_running: false,
      },
    ]);
    render(<SessionView workdir="/wd" />);

    const input = screen.getByLabelText("Agent 消息输入");
    fireEvent.change(input, { target: { value: "/compact" } });
    fireEvent.click(await screen.findByRole("option", { name: /\/compact/ }));

    await waitFor(() => expect(vi.mocked(compactCodexSession)).toHaveBeenCalledWith("s1"));
    expect(await screen.findByText("上下文压缩已启动")).toBeInTheDocument();
    expect(useAgentStore.getState().sessions.s1.turns).toHaveLength(0);
  });

  it("shows the selected workspace home when activeSid is null", () => {
    render(<SessionView workdir="/wd" />);

    expect(screen.getByRole("region", { name: "当前工作区" }))
      .toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "wd" })).toBeInTheDocument();
    expect(screen.queryByLabelText("Agent 消息输入")).toBeNull();
  });

  it("asks for a workdir before opening a new-session dialog", () => {
    const requestWorkdir = vi.fn();
    render(
      <SessionView
        workdir=""
        onRequestChangeWorkdir={requestWorkdir}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "同目录新开" }));

    expect(requestWorkdir).toHaveBeenCalledOnce();
    expect(
      screen.queryByRole("dialog", { name: "新建 Agent 会话" }),
    ).toBeNull();
  });

  it("shows a direct workdir action when Agent has no workdir", () => {
    const requestWorkdir = vi.fn();
    render(
      <SessionView
        workdir=""
        onRequestChangeWorkdir={requestWorkdir}
      />,
    );

    const actions = screen.getAllByRole("button", {
      name: "选择工作目录",
    });
    const primaryAction = actions.find((button) =>
      button.classList.contains("cc-empty__action"),
    );
    expect(primaryAction).toBeDefined();
    fireEvent.click(primaryAction!);

    expect(requestWorkdir).toHaveBeenCalledOnce();
  });

  it("shows the active native session id immediately left of the workdir button", () => {
    const nativeSessionId = "019c1f22-96f2-7341-b85a-2f7244e63526";
    useAgentStore.setState({
      sessions: {
        s1: createNewSessionState(
          {
            session_id: "s1",
            runtime: "codex",
            native_session_id: nativeSessionId,
            workdir: "/wd",
            model: "gpt-5.6-sol",
            effort: "high",
            permission: "Full access · never",
            memory_enabled: true,
            profile_enabled: true,
            capabilities: CODEX_CAPABILITIES,
            name: "wd",
            connected: true,
            running: false,
          },
          { workdir: "/wd", runtime: "codex" },
        ),
      },
      activeSid: "s1",
    });

    render(
      <SessionView workdir="/wd" onRequestChangeWorkdir={() => {}} />,
    );

    const copyButton = screen.getByRole("button", {
      name: `复制会话 ID ${nativeSessionId}`,
    });
    const workdirButton = screen.getByTitle("工作目录：/wd（点击切换）");
    expect(copyButton.nextElementSibling).toBe(workdirButton);
  });

  it("uses the active live session workdir when resuming its history", async () => {
    installCodexSession();
    vi.mocked(listActiveSessions).mockResolvedValueOnce({
      sessions: [codexLiveSession()],
      activeId: "s1",
    });
    vi.mocked(listSessions).mockResolvedValueOnce({
      rows: [
        {
          runtime: "codex",
          native_session_id: "older-thread",
          title: "旧会话",
          updated_at: "2026-08-01T10:00:00+00:00",
        },
      ],
      nextCursor: null,
    });
    render(<SessionView workdir="" />);

    fireEvent.click(screen.getByRole("button", { name: "历史会话" }));
    fireEvent.click(await screen.findByRole("option", { name: /旧会话/ }));

    await waitFor(() => {
      expect(vi.mocked(createSession)).toHaveBeenCalledWith(
        expect.objectContaining({
          workdir: "/wd",
          resume_from: "older-thread",
        }),
        expect.any(String),
      );
    });
  });

  it("shows why an old history row cannot be resumed", async () => {
    installCodexSession();
    vi.mocked(listActiveSessions).mockResolvedValueOnce({
      sessions: [codexLiveSession()],
      activeId: "s1",
    });
    vi.mocked(listSessions).mockResolvedValueOnce({
      rows: [
        {
          runtime: "codex",
          native_session_id: "unknown-thread",
          title: "缺少冻结配置的旧会话",
          updated_at: "2026-08-01T10:00:00+00:00",
        },
      ],
      nextCursor: null,
    });
    vi.mocked(createSession).mockRejectedValueOnce(
      new Error(
        "该历史会话没有可用的冻结连接，暂时无法直接恢复；当前可先新建会话",
      ),
    );
    render(<SessionView workdir="" />);

    fireEvent.click(screen.getByRole("button", { name: "历史会话" }));
    fireEvent.click(
      await screen.findByRole("option", { name: /缺少冻结配置的旧会话/ }),
    );

    expect(
      await screen.findByText(
        "历史会话无法直接恢复：该历史会话没有可用的冻结连接，暂时无法直接恢复；当前可先新建会话",
      ),
    ).toBeVisible();
  });

  it("updates the renderer workspace when a live session is selected", async () => {
    vi.mocked(listActiveSessions).mockResolvedValue({
      sessions: [codexLiveSession()],
      activeId: null,
    });
    const activateWorkdir = vi.fn();
    render(
      <SessionView workdir="" onWorkdirActivated={activateWorkdir} />,
    );

    fireEvent.click((await screen.findByTestId("session-title")).closest("button")!);

    expect(activateWorkdir).toHaveBeenCalledWith("/wd");
  });

  it("discovers a live session created by another renderer after refocus", async () => {
    vi.mocked(listActiveSessions)
      .mockResolvedValueOnce({ sessions: [], activeId: null })
      .mockResolvedValue({ sessions: [codexLiveSession()], activeId: null });
    render(<SessionView workdir="" />);
    await waitFor(() => expect(listActiveSessions).toHaveBeenCalledOnce());

    window.dispatchEvent(new Event("focus"));

    expect(await screen.findByTestId("session-title")).toBeInTheDocument();
    expect(useAgentStore.getState().activeSid).toBeNull();
  });

  it("reconcile 时按后端 connected 字段标记，temp(connected=false) 不进多开栏", async () => {
    vi.mocked(listActiveSessions).mockResolvedValueOnce({
      sessions: [
        { session_id: "temp1", runtime: "claude_code", native_session_id: null, workdir: "/wd", model: "m", effort: null, permission: null, memory_enabled: true, profile_enabled: true, capabilities: CC_CAPABILITIES, name: "wd", connected: false, running: false },
      ],
      activeId: "temp1",
    });
    render(<SessionView workdir="/wd" />);
    await waitFor(() => {
      expect(useAgentStore.getState().sessions["temp1"]).toBeDefined();
    });
    expect(useAgentStore.getState().sessions["temp1"]?.connected).toBe(false);
    expect(screen.getByText(/暂无连接/)).toBeInTheDocument();
  });

  it("workdir 变化时只刷新新目录历史", async () => {
    const { rerender } = render(<SessionView workdir="/a" />);
    await waitFor(() =>
      expect(vi.mocked(listSessions).mock.calls.map(([path]) => path)).toContain(
        "/a",
      ),
    );

    rerender(<SessionView workdir="/b" />);
    await waitFor(() =>
      expect(vi.mocked(listSessions).mock.calls.at(-1)?.[0]).toBe("/b"),
    );
    expect(vi.mocked(createSession)).not.toHaveBeenCalled();
  });

  it("首次进入不创建会话，显式新建时才读取最近实际配置", async () => {
    vi.mocked(getAgentSessionDefaults).mockResolvedValueOnce({
      runtime: "codex",
      model: "gpt-5.6-sol",
      effort: "high",
      permission_mode: "",
      permission_preset: "workspace-write",
      memory_enabled: true,
      profile_enabled: true,
    });

    render(<SessionView workdir="/wd" />);

    expect(vi.mocked(createSession)).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "同目录新开" }));

    await screen.findByRole("dialog", { name: "新建 Agent 会话" });
    expect(vi.mocked(getAgentSessionDefaults)).toHaveBeenCalledOnce();
    expect(screen.getByRole("button", { name: "workspace-write" })).toHaveClass(
      "cc-dialog__option--selected",
    );
    expect(vi.mocked(createSession)).not.toHaveBeenCalled();
  });

  it("double-clicking new session reuses one defaults preparation", async () => {
    const defaults = deferred<null>();
    vi.mocked(getAgentSessionDefaults).mockReturnValueOnce(defaults.promise);
    render(<SessionView workdir="/wd" />);

    const open = screen.getByRole("button", { name: "同目录新开" });
    fireEvent.click(open);
    fireEvent.click(open);

    expect(vi.mocked(getAgentSessionDefaults)).toHaveBeenCalledOnce();
    expect(open).toBeDisabled();
    defaults.resolve(null);
    await screen.findByRole("dialog", { name: "新建 Agent 会话" });
  });

  it("ignores a late defaults response for an older workdir request", async () => {
    const older = deferred<null>();
    const newer = deferred<null>();
    vi.mocked(getAgentSessionDefaults)
      .mockReturnValueOnce(older.promise)
      .mockReturnValueOnce(newer.promise);
    const { rerender } = render(
      <SessionView
        workdir=""
        newSessionWorkdirRequest={{ id: 1, workdir: "/older" }}
      />,
    );
    await waitFor(() => expect(getAgentSessionDefaults).toHaveBeenCalledOnce());
    rerender(
      <SessionView
        workdir=""
        newSessionWorkdirRequest={{ id: 2, workdir: "/newer" }}
      />,
    );
    await waitFor(() => expect(getAgentSessionDefaults).toHaveBeenCalledTimes(2));

    newer.resolve(null);
    await screen.findByRole("dialog", { name: "新建 Agent 会话" });
    older.resolve(null);
    await Promise.resolve();

    expect(screen.getByTitle("/newer")).toBeInTheDocument();
    expect(screen.queryByTitle("/older")).toBeNull();
  });

  it("挂载时只恢复 live session 列表，不自动创建新 session", async () => {
    vi.mocked(listActiveSessions).mockResolvedValueOnce({
      sessions: [
        {
          session_id: "stale",
          runtime: "claude_code",
          native_session_id: "old-native",
          workdir: "/wd",
          model: "opus",
          effort: "max",
          permission: "bypassPermissions",
          memory_enabled: true,
          profile_enabled: true,
          capabilities: ["tools"],
          name: "wd",
          connected: false,
          running: false,
        },
      ],
      activeId: null,
    });
    vi.mocked(getAgentSessionDefaults).mockResolvedValueOnce({
      runtime: "claude_code",
      model: "opus",
      effort: "max",
      permission_mode: "bypassPermissions",
      memory_enabled: true,
      profile_enabled: true,
    });

    render(<SessionView workdir="/wd" />);

    await waitFor(() => expect(vi.mocked(listActiveSessions)).toHaveBeenCalled());
    expect(vi.mocked(createSession)).not.toHaveBeenCalled();
    expect(useAgentStore.getState().activeSid).toBeNull();
  });

  it("选择工作区后刷新该目录历史，但仍不创建 session", async () => {
    render(<SessionView workdir="/fail" />);
    await waitFor(() => {
      const calls = vi.mocked(listSessions).mock.calls.map(([w]) => w);
      expect(calls).toContain("/fail");
    });
    expect(vi.mocked(createSession)).not.toHaveBeenCalled();
  });

  it("renders the Codex host degraded banner when hostDegraded is set", () => {
    useAgentStore.setState({
      sessions: {
        "c1": {
          turns: [],
          phase: "error",
          tasks: [],
          goal: null,
          plan: null,
          turnDiff: null,
          codexSubagents: {},
          meta: {
            model: "gpt-5.6-sol",
            ccSessionId: "thr-1",
          costUsd: null,
          numTurns: null,
          lastTurnTokens: null,
          compactionCount: 0,
          hookFired: null,
            thinkingStartedAt: null,
            thinkingTokens: null,
            stallWarning: null,
            exited: false,
            exitReturncode: null,
            usage: null,
            hostDegraded: true,
            rateLimit: null,
          },
          workdir: "/wd",
          effort: null,
          name: "wd",
          displayTitle: "wd",
          titleSource: "native",
          checkpointAvailable: false,
          transportError: null,
          abort: null,
          connected: true,
          resourceState: "connected",
          turnState: "idle",
          liveState: "ready",
          currentTurnId: null,
          stateGeneration: 1,
          memoryEnabled: true,
          profileEnabled: true,
          runtime: "codex",
          nativeSessionId: "thr-1",
          permission: "workspace-write",
          capabilities: CODEX_CAPABILITIES,
          lastSeq: null,
          needsReplay: false,
        },
      },
      activeSid: "c1",
      history: [],
      historyTotal: 0,
      loadingHistory: false,
    });
    render(<SessionView workdir="/wd" />);
    expect(screen.getByText(/Codex 进程已断开/)).toBeInTheDocument();
    expect(screen.getByText(/不会自动重放写操作/)).toBeInTheDocument();
  });

  it("persists the explicit new-session config only after creation succeeds", async () => {
    vi.mocked(listAgentRuntimes).mockResolvedValue([
      {
        runtime: "claude_code",
        label: "Claude Code",
        native: "claude -p",
        capabilities: CC_CAPABILITIES,
        connected: true,
      },
    ]);
    render(<SessionView workdir="/wd" />);

    fireEvent.click(screen.getByRole("button", { name: "同目录新开" }));
    const high = await screen.findByRole("button", { name: "high" });
    fireEvent.click(high);
    const create = screen.getByRole("button", { name: /^创建/ });
    await waitFor(() => expect(create).toBeEnabled());
    fireEvent.click(create);
    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: "新建 Agent 会话" })).toBeNull(),
    );

    expect(loadNewSessionPreferences()).toMatchObject({
      runtime: "claude_code",
      effort: "high",
      permission_mode: "bypassPermissions",
      memory_enabled: true,
      profile_enabled: true,
    });
  });

  it("新会话弹窗优先继承后端最近实际配置", async () => {
    vi.mocked(getAgentSessionDefaults).mockResolvedValueOnce({
        runtime: "codex",
        model: "gpt-5.6-sol",
        effort: "high",
        permission_mode: "",
        permission_preset: "workspace-write",
        memory_enabled: false,
        profile_enabled: true,
      });
    vi.mocked(listAgentRuntimes).mockResolvedValue([
      {
        runtime: "codex",
        label: "Codex",
        native: "app-server",
        capabilities: CODEX_CAPABILITIES,
        connected: true,
      },
    ]);
    render(<SessionView workdir="/wd" />);

    fireEvent.click(screen.getByRole("button", { name: "同目录新开" }));

    const dialog = await screen.findByRole("dialog", {
      name: "新建 Agent 会话",
    });
    expect(dialog.querySelectorAll('[role="radio"]')[1]).toHaveAttribute(
      "aria-checked",
      "true",
    );
    expect(screen.getByRole("button", { name: "workspace-write" })).toHaveClass(
      "cc-dialog__option--selected",
    );
    expect(screen.getByRole("switch", { name: "Memory 开关" })).toHaveAttribute(
      "aria-checked",
      "false",
    );
  });

  it("每次打开新会话前重新读取供应商设置", async () => {
    vi.mocked(listAgentRuntimes).mockResolvedValue([
      {
        runtime: "codex",
        label: "Codex",
        native: "app-server",
        capabilities: CODEX_CAPABILITIES,
        connected: true,
      },
    ]);
    render(<SessionView workdir="/wd" />);
    expect(listAgentConnectionOptions).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "同目录新开" }));
    await screen.findByRole("dialog", { name: "新建 Agent 会话" });
    expect(listAgentConnectionOptions).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    fireEvent.click(screen.getByRole("button", { name: "同目录新开" }));
    await screen.findByRole("dialog", { name: "新建 Agent 会话" });

    expect(listAgentConnectionOptions).toHaveBeenCalledTimes(2);
  });

  it("does not overwrite the previous config when explicit creation fails", async () => {
    const previous = {
      runtime: "claude_code" as const,
      model: "",
      effort: "low",
      permission_mode: "default",
      memory_enabled: false,
      profile_enabled: true,
    };
    saveNewSessionPreferences(previous);
    vi.mocked(listAgentRuntimes).mockResolvedValue([
      {
        runtime: "claude_code",
        label: "Claude Code",
        native: "claude -p",
        capabilities: CC_CAPABILITIES,
        connected: true,
      },
    ]);
    render(<SessionView workdir="/wd" />);
    vi.mocked(createSession).mockRejectedValueOnce(new Error("backend down"));

    fireEvent.click(screen.getByRole("button", { name: "同目录新开" }));
    const create = await screen.findByRole("button", { name: /^创建/ });
    await waitFor(() => expect(create).toBeEnabled());
    fireEvent.click(create);
    await screen.findByRole("alert");

    expect(loadNewSessionPreferences()).toEqual(previous);
  });
});
