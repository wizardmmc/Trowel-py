/**
 * slice-028 SessionView smoke test: the three-column shell mounts and renders
 * MultiSessionBar (left) + center message area + TodoBar (right) without
 * crashing. Deep interaction is covered by the per-component tests
 * (TodoBar/MultiSessionBar) and the pure-reducer tests; this just guards the
 * wiring (active-session selector, mount effect, layout).
 */
import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

vi.mock("../api/cc", () => ({
  createSession: vi.fn().mockResolvedValue({
    session_id: "s1",
    cc_session_id: null,
    model: "glm-5.2",
    name: "wd",
    revert_enabled: true,
  }),
  activateSession: vi.fn().mockResolvedValue({ active_id: "s1" }),
  deleteSession: vi.fn().mockResolvedValue({ closed: true }),
  listSessions: vi.fn().mockResolvedValue({ sessions: [], total: 0 }),
  listActiveSessions: vi.fn().mockResolvedValue({ sessions: [], activeId: null }),
  listModels: vi.fn().mockResolvedValue([]),
  listSlashItems: vi.fn().mockResolvedValue([]),
  getHistory: vi.fn().mockResolvedValue([]),
  interruptSession: vi.fn().mockResolvedValue({ interrupted: true }),
  revertSession: vi.fn().mockResolvedValue({ reverted_turn_id: "x" }),
  answerElicit: vi.fn().mockResolvedValue({ ok: true }),
  messagesUrl: (sid: string) => `/api/cc/sessions/${sid}/messages`,
}));

import { SessionView } from "../components/cc/SessionView";
import { useCcStore } from "../stores/ccStore";
import { listActiveSessions, createSession, listSessions } from "../api/cc";

beforeEach(() => {
  vi.clearAllMocks();
  useCcStore.setState({
    sessions: {},
    activeSid: null,
    history: [],
    historyTotal: 0,
    loadingHistory: false,
  });
});

describe("SessionView (slice-028 three-column)", () => {
  it("mounts the three-column shell — multi-bar, center, todo-bar all present", async () => {
    const { container } = render(
      <SessionView workdir="/wd" onRequestChangeWorkdir={() => {}} />,
    );
    // the three columns
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
    // the center prompt lives in .cc-empty--noactive (TodoBar also says this,
    // so target the center one specifically)
    expect(container.querySelector(".cc-empty--noactive")).not.toBeNull();
    expect(container.querySelector(".cc-empty--noactive")?.textContent)
      .toMatch(/未选择 session/);
  });

  it("reconcile 时按后端 connected 字段标记，temp(connected=false) 不进多开栏", async () => {
    // bug：多出 ClaudeDesktop 多开 —— 后端返回的 temp 被 reconcile 一律标 live。
    vi.mocked(listActiveSessions).mockResolvedValueOnce({
      sessions: [
        { id: "temp1", workdir: "/wd", model: "m", name: "wd", running: false, connected: false, memory_enabled: true, profile_enabled: true },
      ],
      activeId: "temp1",
    });
    render(<SessionView workdir="/wd" />);
    await waitFor(() => {
      expect(useCcStore.getState().sessions["temp1"]).toBeDefined();
    });
    // temp 的 connected 必须保持 false（不是被 reconcile 写死成 true）
    expect(useCcStore.getState().sessions["temp1"]?.connected).toBe(false);
    // 多开栏只显示 connected 的 → temp 不显示，仍是"暂无连接"
    expect(screen.getByText(/暂无连接/)).toBeInTheDocument();
  });

  it("workdir 变化时立即用新 workdir 新建会话并刷新历史（bug1：切换路径不生效）", async () => {
    // 让每次 createSession 返回以 workdir 区分的 sid，便于断言
    vi.mocked(createSession).mockImplementation(async (params) => ({
      session_id: `sid-${params.workdir}`,
      cc_session_id: null,
      model: "m",
      name: params.workdir,
      revert_enabled: true,
      memory_enabled: true,
      profile_enabled: true,
    }));
    const { rerender } = render(<SessionView workdir="/a" />);
    await waitFor(() => expect(useCcStore.getState().activeSid).toBe("sid-/a"));

    // 切换路径到 /b —— 期望立即新建 /b 会话（而非卡在 /a 的旧 temp）
    rerender(<SessionView workdir="/b" />);
    await waitFor(() => expect(useCcStore.getState().activeSid).toBe("sid-/b"));

    // 主视图 active 的 workdir 是新路径
    expect(useCcStore.getState().sessions["sid-/b"]?.workdir).toBe("/b");
    // 旧 temp /a 被丢弃（dropTempActive）
    expect(useCcStore.getState().sessions["sid-/a"]).toBeUndefined();
    // 历史列表刷新到新路径
    expect(vi.mocked(listSessions).mock.calls.at(-1)?.[0]).toBe("/b");
  });

  it("startSession 失败时历史仍刷新到当前 workdir（兜底，不停留在旧路径）", async () => {
    vi.mocked(createSession).mockRejectedValueOnce(new Error("backend down"));
    render(<SessionView workdir="/fail" />);
    // 即使创建会话失败（active 为 null），历史下拉框也要刷到当前 workdir
    await waitFor(() => {
      const calls = vi.mocked(listSessions).mock.calls.map(([w]) => w);
      expect(calls).toContain("/fail");
    });
  });
});
