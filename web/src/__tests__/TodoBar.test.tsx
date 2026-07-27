import { describe, it, expect, beforeEach, vi } from "vitest";
import { act, render, screen, fireEvent } from "@testing-library/react";

import { TodoBar } from "../components/cc/TodoBar";
import {
  useCcStore,
  INITIAL_REDUCER_STATE,
  type PerSessionState,
  type Task,
} from "../stores/ccStore";

const SID = "s1";

function makeSession(
  tasks: Task[],
  over: Partial<PerSessionState> = {},
): PerSessionState {
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
    tasks,
    ...over,
    codexSubagents: over.codexSubagents ?? {},
  };
}

function setActive(session: PerSessionState | null): void {
  if (session) {
    useCcStore.setState({ sessions: { [SID]: session }, activeSid: SID });
  } else {
    useCcStore.setState({ sessions: {}, activeSid: null });
  }
}

beforeEach(() => {
  useCcStore.setState({
    sessions: {},
    activeSid: null,
    history: [],
    historyTotal: 0,
    loadingHistory: false,
  });
});

describe("TodoBar", () => {
  it("shows the idle hint when there is no active session", () => {
    setActive(null);
    render(<TodoBar />);
    expect(screen.getByText("未选择 session")).toBeInTheDocument();
  });

  it("shows the empty hint when the active session has no tasks", () => {
    setActive(makeSession([]));
    render(<TodoBar />);
    expect(screen.getByText("本 session 暂无任务")).toBeInTheDocument();
  });

  it("renders each pending/in-progress task with the right icon", () => {
    setActive(
      makeSession([
        { taskId: "1", toolUseId: "tu_1", subject: "写后端", status: "in_progress", activeForm: "写后端中" },
        { taskId: "2", toolUseId: "tu_2", subject: "写前端", status: "pending" },
      ]),
    );
    render(<TodoBar />);
    expect(screen.getByText("写后端")).toBeInTheDocument();
    expect(screen.getByText("写前端")).toBeInTheDocument();
    expect(screen.getByText("0/2")).toBeInTheDocument();
    expect(screen.getByText("写后端中")).toBeInTheDocument();
  });

  it("collapses completed tasks behind a toggle and counts them", () => {
    setActive(
      makeSession([
        { taskId: "1", toolUseId: "tu_1", subject: "做 A", status: "completed" },
        { taskId: "2", toolUseId: "tu_2", subject: "做 B", status: "in_progress" },
      ]),
    );
    render(<TodoBar />);
    expect(screen.queryByText("做 A")).toBeNull();
    expect(screen.getByText(/已完成 1 项/)).toBeInTheDocument();
    expect(screen.getByText("1/2")).toBeInTheDocument();

    fireEvent.click(screen.getByText(/已完成 1 项/));
    expect(screen.getByText("做 A")).toBeInTheDocument();
  });

  it("updates when the store tasks change (增量更新)", () => {
    setActive(makeSession([
      { taskId: "1", toolUseId: "tu_1", subject: "X", status: "pending" },
    ]));
    const { rerender } = render(<TodoBar />);
    expect(screen.getByText("0/1")).toBeInTheDocument();

    setActive(
      makeSession([
        { taskId: "1", toolUseId: "tu_1", subject: "X", status: "completed" },
      ]),
    );
    rerender(<TodoBar />);
    expect(screen.getByText("1/1")).toBeInTheDocument();
    expect(screen.getByText(/已完成 1 项/)).toBeInTheDocument();
  });

  it("shows native Goal and Plan for Codex instead of Claude tasks", () => {
    setActive(
      makeSession(
        [{ taskId: "cc", toolUseId: "cc", subject: "不应显示", status: "pending" }],
        {
          runtime: "codex",
          goal: {
            objective: "Ship Goal and Plan",
            status: "active",
            tokenBudget: 12000,
            tokensUsed: 7448,
            timeUsedSeconds: 9,
            createdAt: 10,
            updatedAt: 11,
          },
          plan: {
            explanation: null,
            steps: [
              { step: "Inspect native event", status: "completed" },
              { step: "Map the UI state", status: "inProgress" },
              { step: "Report verification", status: "pending" },
            ],
          },
        },
      ),
    );

    render(<TodoBar />);

    expect(screen.getByText("目标与计划")).toBeInTheDocument();
    expect(screen.getByText("Ship Goal and Plan")).toBeInTheDocument();
    expect(screen.getByText("Map the UI state")).toBeInTheDocument();
    expect(screen.getByText("1 / 3")).toBeInTheDocument();
    expect(screen.queryByText("不应显示")).toBeNull();
  });

  it("keeps Claude Code on the native Task list", () => {
    setActive(
      makeSession(
        [{ taskId: "1", toolUseId: "1", subject: "Claude Task", status: "pending" }],
        {
          goal: {
            objective: "Codex-only Goal",
            status: "active",
            tokenBudget: null,
            tokensUsed: 0,
            timeUsedSeconds: 0,
            createdAt: 1,
            updatedAt: 1,
          },
        },
      ),
    );

    render(<TodoBar />);

    expect(screen.getByText("待办")).toBeInTheDocument();
    expect(screen.getByText("Claude Task")).toBeInTheDocument();
    expect(screen.queryByText("Codex-only Goal")).toBeNull();
  });

  it("does not offer to resume a completed Goal", () => {
    setActive(
      makeSession([], {
        runtime: "codex",
        goal: {
          objective: "Finished Goal",
          status: "complete",
          tokenBudget: 12000,
          tokensUsed: 8000,
          timeUsedSeconds: 10,
          createdAt: 1,
          updatedAt: 2,
        },
      }),
    );

    render(<TodoBar />);

    expect(screen.getByText("已完成")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "恢复 Goal" })).toBeNull();
    expect(screen.getByRole("button", { name: "编辑 Goal" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "清除 Goal" })).toBeInTheDocument();
  });

  it("opens the native Goal editor and exposes the mobile drawer close", () => {
    const onClose = vi.fn();
    setActive(
      makeSession([], {
        runtime: "codex",
        goal: {
          objective: "Editable Goal",
          status: "paused",
          tokenBudget: null,
          tokensUsed: 0,
          timeUsedSeconds: 0,
          createdAt: 1,
          updatedAt: 1,
        },
      }),
    );

    render(<TodoBar drawerOpen onCloseDrawer={onClose} />);
    expect(screen.getByLabelText("Codex 目标与计划")).toHaveClass(
      "cc-workrail--open",
    );
    fireEvent.click(screen.getByRole("button", { name: "编辑 Goal" }));
    expect(screen.getByRole("textbox", { name: "Goal 目标" })).toHaveValue(
      "Editable Goal",
    );
    fireEvent.click(screen.getByRole("button", { name: "关闭目标与计划" }));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("drops an open Goal draft when the active Codex session changes", () => {
    setActive(
      makeSession([], {
        runtime: "codex",
        goal: {
          objective: "First Goal",
          status: "paused",
          tokenBudget: null,
          tokensUsed: 0,
          timeUsedSeconds: 0,
          createdAt: 1,
          updatedAt: 1,
        },
      }),
    );
    render(<TodoBar />);
    fireEvent.click(screen.getByRole("button", { name: "编辑 Goal" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Goal 目标" }), {
      target: { value: "Unsaved draft" },
    });

    act(() => {
      useCcStore.setState({
        sessions: {
          s2: makeSession([], {
            runtime: "codex",
            goal: {
              objective: "Second Goal",
              status: "active",
              tokenBudget: 8000,
              tokensUsed: 0,
              timeUsedSeconds: 0,
              createdAt: 2,
              updatedAt: 2,
            },
          }),
        },
        activeSid: "s2",
      });
    });

    expect(screen.queryByRole("textbox", { name: "Goal 目标" })).toBeNull();
    expect(screen.getByText("Second Goal")).toBeInTheDocument();
    expect(screen.queryByDisplayValue("Unsaved draft")).toBeNull();
  });
});
