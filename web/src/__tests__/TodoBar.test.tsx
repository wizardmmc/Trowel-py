import { describe, it, expect, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

import { TodoBar } from "../components/cc/TodoBar";
import {
  useCcStore,
  INITIAL_REDUCER_STATE,
  type PerSessionState,
  type Task,
} from "../stores/ccStore";

const SID = "s1";

function makeSession(tasks: Task[]): PerSessionState {
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
    tasks,
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

describe("TodoBar (slice-028)", () => {
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
    // progress 0/2 (no completed yet)
    expect(screen.getByText("0/2")).toBeInTheDocument();
    // activeForm surfaces for the in_progress task
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
    // completed subject hidden until expanded
    expect(screen.queryByText("做 A")).toBeNull();
    expect(screen.getByText(/已完成 1 项/)).toBeInTheDocument();
    expect(screen.getByText("1/2")).toBeInTheDocument();

    // expand → completed subject appears
    fireEvent.click(screen.getByText(/已完成 1 项/));
    expect(screen.getByText("做 A")).toBeInTheDocument();
  });

  it("updates when the store tasks change (增量更新)", () => {
    setActive(makeSession([
      { taskId: "1", toolUseId: "tu_1", subject: "X", status: "pending" },
    ]));
    const { rerender } = render(<TodoBar />);
    expect(screen.getByText("0/1")).toBeInTheDocument();

    // simulate a TaskUpdate flipping X to completed
    setActive(
      makeSession([
        { taskId: "1", toolUseId: "tu_1", subject: "X", status: "completed" },
      ]),
    );
    rerender(<TodoBar />);
    expect(screen.getByText("1/1")).toBeInTheDocument();
    expect(screen.getByText(/已完成 1 项/)).toBeInTheDocument();
  });
});
