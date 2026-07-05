/**
 * slice-028 SessionView smoke test: the three-column shell mounts and renders
 * MultiSessionBar (left) + center message area + TodoBar (right) without
 * crashing. Deep interaction is covered by the per-component tests
 * (TodoBar/MultiSessionBar) and the pure-reducer tests; this just guards the
 * wiring (active-session selector, mount effect, layout).
 */
import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen } from "@testing-library/react";

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
});
