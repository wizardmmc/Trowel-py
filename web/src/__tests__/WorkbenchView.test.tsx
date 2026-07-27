import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { WorkbenchState } from "../api/modelOs";
import { WorkbenchView } from "../components/workbench/WorkbenchView";
import { useWorkbenchStore } from "../stores/workbenchStore";

vi.mock("../api/modelOs", async () => {
  const actual = await vi.importActual<typeof import("../api/modelOs")>(
    "../api/modelOs",
  );
  return {
    ...actual,
    fetchWorkbench: vi.fn(),
    setWorkbenchAutomation: vi.fn(),
    setWorkbenchTaskWarm: vi.fn(),
    setWorkbenchTaskPriority: vi.fn(),
    requestWorkbenchForeground: vi.fn(),
    recordWorkbenchCandidateOutcome: vi.fn(),
    sendWorkbenchInstruction: vi.fn(),
    replyWorkbenchWaiting: vi.fn(),
    subscribeWorkbench: vi.fn(() => () => undefined),
  };
});

import {
  fetchWorkbench,
  recordWorkbenchCandidateOutcome,
  replyWorkbenchWaiting,
  sendWorkbenchInstruction,
  setWorkbenchAutomation,
} from "../api/modelOs";

const state: WorkbenchState = {
  as_of: { event_seq: 18, decision_seq: 4 },
  automation_paused: false,
  foreground_task_id: "task-current",
  next_task_id: "task-next",
  tasks: [
    {
      task_id: "task-current",
      goal: "实现 Model OS 工作台",
      constraints: ["按确认后的原型实现"],
      status: "running",
      priority: 8,
      warm: true,
      warm_rank: 1,
      is_foreground: true,
      waiting: null,
      episode_id: "episode-current",
      episode_status: "active",
      agent_session_id: "session-current",
      runtime: "codex",
      model: "gpt-5.6",
      effort: "high",
      connected: true,
      running: true,
      can_send_message: true,
      current_judgment: "工作台读取模型已经接通",
      next_steps: ["完成前端页面", "执行浏览器走查"],
      updated_at: "2026-07-27T02:00:00+00:00",
    },
    {
      task_id: "task-next",
      goal: "完成综合耐久验证",
      constraints: [],
      status: "ready",
      priority: 6,
      warm: true,
      warm_rank: 2,
      is_foreground: false,
      waiting: null,
      episode_id: null,
      episode_status: null,
      agent_session_id: null,
      runtime: null,
      model: null,
      effort: null,
      connected: null,
      running: null,
      can_send_message: false,
      current_judgment: null,
      next_steps: [],
      updated_at: "2026-07-27T01:00:00+00:00",
    },
    {
      task_id: "task-waiting",
      goal: "冻结生产交互",
      constraints: [],
      status: "waiting_user",
      priority: 5,
      warm: true,
      warm_rank: 3,
      is_foreground: false,
      waiting: {
        kind: "waiting_user",
        cause: "这版通过后，是冻结设计还是继续实现？",
        subtype: "input",
        episode_id: "episode-waiting",
        correlation_id: "wait-1",
        deadline: null,
        condition_kind: null,
        target_ref: null,
        open_question: null,
        earliest_review_at: null,
      },
      pending_request: {
        kind: "input",
        request_id: "wait-1",
        prompt: "这版通过后，是冻结设计还是继续实现？",
        questions: [
          {
            question: "这版通过后，是冻结设计还是继续实现？",
            header: "下一步",
            options: [],
            multiSelect: false,
          },
        ],
        available_decisions: [],
      },
      episode_id: "episode-waiting",
      episode_status: "suspended_waiting_input",
      agent_session_id: "session-waiting",
      runtime: "claude_code",
      model: "opus",
      effort: null,
      connected: true,
      running: false,
      can_send_message: true,
      current_judgment: "等待确认实现范围",
      next_steps: [],
      updated_at: "2026-07-27T01:30:00+00:00",
    },
  ],
  candidates: [
    {
      candidate_id: "candidate-1",
      source_kind: "default",
      task_id: null,
      title: "把等待原因收敛成稳定分类",
      related_question: "怎样减少状态误判",
      source_refs: ["memory://notes/a"],
      why_useful: "页面能直接解释等待原因",
      new_points: [],
      verification: "用真实等待状态逐一走查",
      uncertainty: "资源等待是否单列仍未确定",
      status: "new",
      runtime: "codex",
      effective_model: "gpt-5.6",
      tier: "deep",
      created_at: "2026-07-27T01:45:00+00:00",
    },
  ],
  recent_events: [
    {
      stream: "decision",
      stream_seq: 4,
      entry_id: "decision-4",
      kind: "attention.schedule",
      recorded_at: "2026-07-27T02:00:00+00:00",
      task_id: "task-current",
      episode_id: "episode-current",
      outcome: null,
      reason: "active_focus",
    },
  ],
};

beforeEach(() => {
  vi.clearAllMocks();
  useWorkbenchStore.setState({
    snapshot: null,
    loading: false,
    actionPending: null,
    error: null,
  });
  vi.mocked(fetchWorkbench).mockResolvedValue(state);
  vi.mocked(setWorkbenchAutomation).mockResolvedValue({
    ...state,
    automation_paused: true,
  });
  vi.mocked(sendWorkbenchInstruction).mockResolvedValue(undefined);
  vi.mocked(replyWorkbenchWaiting).mockResolvedValue(undefined);
  vi.mocked(recordWorkbenchCandidateOutcome).mockResolvedValue(undefined);
});

describe("WorkbenchView", () => {
  it("renders the authoritative current task, next task, model and inbox", async () => {
    render(<WorkbenchView />);

    expect(
      (await screen.findAllByText("实现 Model OS 工作台")).length,
    ).toBeGreaterThan(0);
    expect(
      screen.getAllByText("完成综合耐久验证").length,
    ).toBeGreaterThan(0);
    expect(screen.getByText("Codex · gpt-5.6")).toBeInTheDocument();
    expect(
      screen.getAllByText("这版通过后，是冻结设计还是继续实现？").length,
    ).toBeGreaterThan(0);
    expect(screen.getByText("把等待原因收敛成稳定分类")).toBeInTheDocument();
  });

  it("sends the global instruction to the foreground session and reloads", async () => {
    render(<WorkbenchView />);
    const input = await screen.findByLabelText("给 Trowel 新指令");
    await userEvent.type(input, "先完成回复交互，再做浏览器验证");
    await userEvent.click(screen.getByRole("button", { name: "发送新指令" }));

    await waitFor(() =>
      expect(sendWorkbenchInstruction).toHaveBeenCalledWith(
        "task-current",
        "先完成回复交互，再做浏览器验证",
      ),
    );
    expect(fetchWorkbench).toHaveBeenCalledTimes(2);
  });

  it("replies inside the waiting item without changing the foreground target", async () => {
    render(<WorkbenchView />);
    const reply = await screen.findByLabelText("回复：冻结生产交互");
    await userEvent.type(reply, "继续实现，但不要展示指标");
    await userEvent.click(screen.getByRole("button", { name: "回复“冻结生产交互”" }));

    await waitFor(() =>
      expect(replyWorkbenchWaiting).toHaveBeenCalledWith(
        "task-waiting",
        "wait-1",
        {
          answers: {
            "这版通过后，是冻结设计还是继续实现？":
              "继续实现，但不要展示指标",
          },
        },
      ),
    );
  });

  it("records candidate outcome and automation pause through backend commands", async () => {
    render(<WorkbenchView />);
    await screen.findByText("把等待原因收敛成稳定分类");

    await userEvent.click(screen.getByRole("button", { name: "采纳候选" }));
    expect(recordWorkbenchCandidateOutcome).toHaveBeenCalledWith(
      "default",
      "candidate-1",
      "adopted",
    );

    await userEvent.click(screen.getByRole("button", { name: "进入手动模式" }));
    expect(setWorkbenchAutomation).toHaveBeenCalledWith(true);
  });

  it("requires a reason before marking a candidate invalid", async () => {
    render(<WorkbenchView />);
    await screen.findByText("把等待原因收敛成稳定分类");

    await userEvent.click(screen.getByRole("button", { name: "标记有误" }));
    const confirm = screen.getByRole("button", { name: "确认" });
    expect(confirm).toBeDisabled();

    await userEvent.type(screen.getByLabelText("错误原因"), "来源已经失效");
    await userEvent.click(confirm);

    expect(recordWorkbenchCandidateOutcome).toHaveBeenCalledWith(
      "default",
      "candidate-1",
      "invalid",
      "来源已经失效",
    );
  });
});
