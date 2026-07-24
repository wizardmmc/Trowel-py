import { describe, it, expect, beforeEach, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

vi.mock("../api/agent", () => ({
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
    capabilities: ["tools", "approval", "checkpoint", "workflow"],
    name: "wd",
    connected: false,
    running: false,
  }),
  activateAgentSession: vi.fn().mockResolvedValue({ activeId: "s1" }),
  deleteAgentSession: vi.fn().mockResolvedValue({ closed: true }),
  listAgentHistory: vi.fn().mockResolvedValue({ rows: [], nextCursor: null }),
  listActiveAgentSessions: vi.fn().mockResolvedValue({ sessions: [], activeId: null }),
  getAgentSessionDefaults: vi.fn().mockResolvedValue(null),
  listAgentRuntimes: vi.fn().mockResolvedValue([]),
  listAgentModels: vi.fn().mockResolvedValue([]),
  listAgentRequests: vi.fn().mockResolvedValue([]),
  updateAgentSessionSettings: vi.fn(),
  interruptAgentSession: vi.fn().mockResolvedValue({ interrupted: true }),
  answerAgentRequest: vi.fn(),
  agentMessagesUrl: (sid: string) => `/api/agent/sessions/${sid}/messages`,
}));

vi.mock("../api/cc", () => ({
  listModels: vi.fn().mockResolvedValue([]),
  listSlashItems: vi.fn().mockResolvedValue([]),
  getHistory: vi.fn().mockResolvedValue([]),
  revertSession: vi.fn().mockResolvedValue({ reverted_turn_id: "x" }),
  answerElicit: vi.fn().mockResolvedValue({ ok: true }),
}));

import { SessionView } from "../components/cc/SessionView";
import { useCcStore } from "../stores/ccStore";
import {
  createAgentSession as createSession,
  getAgentSessionDefaults,
  listAgentHistory as listSessions,
  listActiveAgentSessions as listActiveSessions,
  listAgentRuntimes,
} from "../api/agent";
import {
  loadNewSessionPreferences,
  saveNewSessionPreferences,
} from "../components/cc/newSessionPreferences";

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(getAgentSessionDefaults).mockResolvedValue(null);
  localStorage.clear();
  useCcStore.setState({
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

  it("shows the no-active-session prompt in the center when activeSid is null", () => {
    const { container } = render(<SessionView workdir="/wd" />);
    expect(container.querySelector(".cc-empty--noactive")).not.toBeNull();
    expect(container.querySelector(".cc-empty--noactive")?.textContent)
      .toMatch(/未选择 session/);
  });

  it("reconcile 时按后端 connected 字段标记，temp(connected=false) 不进多开栏", async () => {
    vi.mocked(listActiveSessions).mockResolvedValueOnce({
      sessions: [
        { session_id: "temp1", runtime: "claude_code", native_session_id: null, workdir: "/wd", model: "m", effort: null, permission: null, memory_enabled: true, profile_enabled: true, capabilities: ["tools", "approval", "checkpoint", "workflow"], name: "wd", connected: false, running: false },
      ],
      activeId: "temp1",
    });
    render(<SessionView workdir="/wd" />);
    await waitFor(() => {
      expect(useCcStore.getState().sessions["temp1"]).toBeDefined();
    });
    expect(useCcStore.getState().sessions["temp1"]?.connected).toBe(false);
    expect(screen.getByText(/暂无连接/)).toBeInTheDocument();
  });

  it("workdir 变化时立即用新 workdir 新建会话并刷新历史", async () => {
    vi.mocked(createSession).mockImplementation(async (params) => ({
      session_id: `sid-${params.workdir}`,
      runtime: "claude_code",
      native_session_id: null,
      workdir: params.workdir,
      model: "m",
      effort: null,
      permission: null,
      memory_enabled: true,
      profile_enabled: true,
      capabilities: ["tools", "approval", "checkpoint", "workflow"],
      name: params.workdir,
      connected: false,
      running: false,
    }));
    const { rerender } = render(<SessionView workdir="/a" />);
    await waitFor(() => expect(useCcStore.getState().activeSid).toBe("sid-/a"));

    rerender(<SessionView workdir="/b" />);
    await waitFor(() => expect(useCcStore.getState().activeSid).toBe("sid-/b"));

    expect(useCcStore.getState().sessions["sid-/b"]?.workdir).toBe("/b");
    expect(useCcStore.getState().sessions["sid-/a"]).toBeUndefined();
    expect(vi.mocked(listSessions).mock.calls.at(-1)?.[0]).toBe("/b");
  });

  it("首次进入 Agent 页时用最近实际配置自动创建可发送会话", async () => {
    vi.mocked(getAgentSessionDefaults).mockResolvedValueOnce({
      runtime: "codex",
      model: "gpt-5.6-sol",
      effort: "high",
      permission_mode: "",
      permission_preset: "workspace-write",
      memory_enabled: false,
      profile_enabled: true,
    });

    render(<SessionView workdir="/wd" />);

    await waitFor(() =>
      expect(vi.mocked(createSession)).toHaveBeenCalledWith({
        workdir: "/wd",
        runtime: "codex",
        model: "gpt-5.6-sol",
        effort: "high",
        permission_mode: "",
        permission_preset: "workspace-write",
        memory_enabled: false,
        profile_enabled: true,
      }),
    );
  });

  it("后端重启后不把 stale binding 当 active，而是按最近配置新建", async () => {
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

    await waitFor(() => expect(vi.mocked(createSession)).toHaveBeenCalled());
    expect(useCcStore.getState().activeSid).not.toBe("stale");
  });

  it("startSession 失败时历史仍刷新到当前 workdir（兜底，不停留在旧路径）", async () => {
    vi.mocked(createSession).mockRejectedValueOnce(new Error("backend down"));
    render(<SessionView workdir="/fail" />);
    await waitFor(() => {
      const calls = vi.mocked(listSessions).mock.calls.map(([w]) => w);
      expect(calls).toContain("/fail");
    });
  });

  it("renders the Codex host degraded banner when hostDegraded is set", () => {
    useCcStore.setState({
      sessions: {
        "c1": {
          turns: [],
          phase: "error",
          tasks: [],
          meta: {
            model: "gpt-5.6-sol",
            ccSessionId: "thr-1",
            costUsd: null,
            numTurns: null,
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
          revertEnabled: false,
          transportError: null,
          abort: null,
          connected: true,
          memoryEnabled: true,
          profileEnabled: true,
          runtime: "codex",
          nativeSessionId: "thr-1",
          permission: "workspace-write",
          capabilities: ["tools", "approval"],
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
    expect(screen.getByText(/Codex host 已断开/)).toBeInTheDocument();
    expect(screen.getByText(/不会自动重放写操作/)).toBeInTheDocument();
  });

  it("persists the explicit new-session config only after creation succeeds", async () => {
    vi.mocked(listAgentRuntimes).mockResolvedValue([
      {
        runtime: "claude_code",
        label: "Claude Code",
        native: "claude -p",
        capabilities: [],
        connected: true,
      },
    ]);
    render(<SessionView workdir="/wd" />);
    await waitFor(() => expect(useCcStore.getState().activeSid).not.toBeNull());

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
    vi.mocked(getAgentSessionDefaults)
      .mockResolvedValueOnce(null)
      .mockResolvedValueOnce({
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
        capabilities: [],
        connected: true,
      },
    ]);
    render(<SessionView workdir="/wd" />);
    await waitFor(() => expect(useCcStore.getState().activeSid).not.toBeNull());

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
        capabilities: [],
        connected: true,
      },
    ]);
    render(<SessionView workdir="/wd" />);
    await waitFor(() => expect(useCcStore.getState().activeSid).not.toBeNull());
    vi.mocked(createSession).mockRejectedValueOnce(new Error("backend down"));

    fireEvent.click(screen.getByRole("button", { name: "同目录新开" }));
    const create = await screen.findByRole("button", { name: /^创建/ });
    await waitFor(() => expect(create).toBeEnabled());
    fireEvent.click(create);
    await screen.findByRole("alert");

    expect(loadNewSessionPreferences()).toEqual(previous);
  });
});
