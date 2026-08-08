/** 验证封闭轮次、共同公开失败态和参与者稳定顺序。 */

import { fireEvent, render, screen } from "@testing-library/react";
import { act } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  applyAttemptEvent,
  createLiveAttemptTimeline,
  type DiscussionAttemptTimeline,
  type DiscussionParticipant,
  type DiscussionRound,
} from "../discussion/domain";
import type { AgentEvent } from "../agent/transport/agentEvent";
import { DiscussionRoundTimeline } from "../discussion/ui/DiscussionRoundTimeline";

const participants: readonly DiscussionParticipant[] = [
  participant("p1", 0, "分析者"),
  participant("p2", 1, "反方"),
  participant("p3", 2, "核查者"),
];

describe("DiscussionRoundTimeline", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("does not leak saved final content when no live timeline is available", () => {
    render(
      <DiscussionRoundTimeline
        participants={participants}
        rounds={[round("running")]}
        disabled={false}
        onMark={() => {}}
      />,
    );

    expect(screen.queryByText("隐藏正文一")).toBeNull();
    expect(screen.getAllByText("正在建立实时轨迹…")).toHaveLength(2);
    expect(screen.getByText("连接已断开")).toBeInTheDocument();
    expect(screen.getByText("1/3 等待共同公开")).toBeInTheDocument();
  });

  it("publishes all terminal slots in stable order and keeps a failed slot", () => {
    const onMark = vi.fn();
    const { container } = render(
      <DiscussionRoundTimeline
        participants={participants}
        rounds={[round("published")]}
        disabled={false}
        onMark={onMark}
      />,
    );

    expect(
      [...container.querySelectorAll(".discussion-response")].map((node) =>
        node.getAttribute("data-participant-id"),
      ),
    ).toEqual(["p1", "p2", "p3"]);
    expect(screen.getByText("连接已断开")).toBeInTheDocument();
    expect(screen.getByText("3/3 同时公开")).toBeInTheDocument();
    expect(screen.getAllByText("10 秒 · 2 次工具")).toHaveLength(3);

    fireEvent.click(screen.getAllByRole("button", { name: "标记" })[0]);
    expect(onMark).toHaveBeenCalledWith(1, "p1", true);
  });

  it("renders a live participant turn from the shared Agent reducer", () => {
    const timeline = timelineFrom([
      agentEvent(10, "turn_start", {}),
      agentEvent(11, "thinking", { text: "正在核对约束" }),
      agentEvent(12, "text", { text: "实时形成的结论" }),
    ]);
    const base = round("running");
    const liveRound: DiscussionRound = {
      ...base,
      participants: base.participants.map((item, index) =>
        index === 0
          ? { ...item, status: "running", current_attempt_id: "attempt-live" }
          : item,
      ),
    };

    render(
      <DiscussionRoundTimeline
        participants={participants}
        rounds={[liveRound]}
        attemptTimelines={{ "attempt-live": timeline }}
        disabled={false}
        onMark={() => {}}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /Thought/ }));
    expect(screen.getByText("正在核对约束")).toBeInTheDocument();
    expect(screen.getByText("实时形成的结论")).toBeInTheDocument();
  });

  it("keeps each live participant card at its latest content until the user scrolls up", () => {
    const frames: FrameRequestCallback[] = [];
    vi.spyOn(window, "requestAnimationFrame").mockImplementation((callback) => {
      frames.push(callback);
      return frames.length;
    });
    const base = round("running");
    const liveRound: DiscussionRound = {
      ...base,
      participants: base.participants.map((item, index) =>
        index === 0
          ? { ...item, status: "running", current_attempt_id: "attempt-live" }
          : item,
      ),
    };
    const firstTimeline = timelineFrom([
      agentEvent(10, "turn_start", {}),
      agentEvent(11, "text", { text: "第一段" }),
    ]);
    const { container, rerender } = render(
      <DiscussionRoundTimeline
        participants={participants}
        rounds={[liveRound]}
        attemptTimelines={{ "attempt-live": firstTimeline }}
        disabled={false}
        onMark={() => {}}
      />,
    );
    const body = container.querySelector<HTMLElement>(
      '[data-participant-id="p1"] .discussion-response__body',
    );
    expect(body).not.toBeNull();
    let scrollTop = 120;
    Object.defineProperties(body as HTMLElement, {
      scrollHeight: { configurable: true, get: () => 900 },
      clientHeight: { configurable: true, get: () => 300 },
      scrollTop: {
        configurable: true,
        get: () => scrollTop,
        set: (value: number) => {
          scrollTop = value;
        },
      },
    });
    const scrollTo = vi.fn((options: ScrollToOptions) => {
      scrollTop = Number(options.top ?? scrollTop);
    });
    Object.defineProperty(body as HTMLElement, "scrollTo", {
      configurable: true,
      value: scrollTo,
    });
    act(() => {
      while (frames.length > 0) frames.shift()?.(16);
    });
    scrollTo.mockClear();

    const grownTimeline = timelineFrom([
      agentEvent(10, "turn_start", {}),
      agentEvent(11, "text", { text: "第一段" }),
      agentEvent(12, "text", { text: "第二段" }),
    ]);
    rerender(
      <DiscussionRoundTimeline
        participants={participants}
        rounds={[liveRound]}
        attemptTimelines={{ "attempt-live": grownTimeline }}
        disabled={false}
        onMark={() => {}}
      />,
    );
    act(() => {
      while (frames.length > 0) frames.shift()?.(32);
    });
    expect(scrollTo).toHaveBeenCalledWith({ top: 900, behavior: "auto" });

    scrollTop = 400;
    fireEvent.wheel(body as HTMLElement, { deltaY: -100 });
    scrollTo.mockClear();
    const pausedTimeline = timelineFrom([
      agentEvent(10, "turn_start", {}),
      agentEvent(11, "text", { text: "第一段" }),
      agentEvent(12, "text", { text: "第二段" }),
      agentEvent(13, "text", { text: "阅读时到达的新内容" }),
    ]);
    rerender(
      <DiscussionRoundTimeline
        participants={participants}
        rounds={[liveRound]}
        attemptTimelines={{ "attempt-live": pausedTimeline }}
        disabled={false}
        onMark={() => {}}
      />,
    );
    act(() => {
      while (frames.length > 0) frames.shift()?.(48);
    });
    expect(scrollTo).not.toHaveBeenCalled();
    expect(scrollTop).toBe(400);
  });

  it("shows elapsed thinking feedback for a live Claude Code participant", () => {
    vi.useFakeTimers();
    vi.setSystemTime(10_000);
    const claudeParticipants = participants.map((item, index) =>
      index === 0 ? { ...item, runtime: "claude_code" as const } : item,
    );
    const timeline = timelineFrom(
      [
        agentEvent(10, "turn_start", {}, null, "claude_code"),
        agentEvent(
          11,
          "thinking_progress",
          { estimated_tokens: 99 },
          null,
          "claude_code",
        ),
      ],
      "claude_code",
    );
    const base = round("running");
    const liveRound: DiscussionRound = {
      ...base,
      participants: base.participants.map((item, index) =>
        index === 0
          ? { ...item, status: "running", current_attempt_id: "attempt-live" }
          : item,
      ),
    };

    render(
      <DiscussionRoundTimeline
        participants={claudeParticipants}
        rounds={[liveRound]}
        attemptTimelines={{ "attempt-live": timeline }}
        disabled={false}
        onMark={() => {}}
      />,
    );

    expect(screen.getByTestId("cc-spinner")).toBeInTheDocument();
    act(() => {
      vi.setSystemTime(16_000);
      vi.advanceTimersByTime(200);
    });
    expect(screen.getByText(/^6 秒$/)).toBeInTheDocument();
    expect(screen.getByText(/高强度思考/)).toBeInTheDocument();
  });

  it("starts Claude Code feedback from the attempt when the CLI stays silent", () => {
    vi.useFakeTimers();
    vi.setSystemTime(10_000);
    const claudeParticipants = participants.map((item, index) =>
      index === 0 ? { ...item, runtime: "claude_code" as const } : item,
    );
    const base = round("running");
    const silentRound: DiscussionRound = {
      ...base,
      participants: base.participants.map((item, index) =>
        index === 0
          ? {
              ...item,
              status: "running",
              current_attempt_id: "attempt-silent",
              started_at: new Date(4_000).toISOString(),
              completed_at: null,
            }
          : item,
      ),
    };

    render(
      <DiscussionRoundTimeline
        participants={claudeParticipants}
        rounds={[silentRound]}
        disabled={false}
        onMark={() => {}}
      />,
    );

    expect(screen.getByTestId("cc-spinner")).toBeInTheDocument();
    expect(screen.getByText(/^6 秒$/)).toBeInTheDocument();
    expect(screen.getByText(/高强度思考/)).toBeInTheDocument();
  });

  it("does not reset elapsed work when a late Claude heartbeat arrives", () => {
    vi.useFakeTimers();
    vi.setSystemTime(10_000);
    const claudeParticipants = participants.map((item, index) =>
      index === 0 ? { ...item, runtime: "claude_code" as const } : item,
    );
    const timeline = timelineFrom(
      [agentEvent(10, "thinking_progress", {}, null, "claude_code")],
      "claude_code",
    );
    const base = round("running");
    const liveRound: DiscussionRound = {
      ...base,
      participants: base.participants.map((item, index) =>
        index === 0
          ? {
              ...item,
              status: "running",
              current_attempt_id: "attempt-live",
              started_at: new Date(4_000).toISOString(),
              completed_at: null,
            }
          : item,
      ),
    };

    render(
      <DiscussionRoundTimeline
        participants={claudeParticipants}
        rounds={[liveRound]}
        attemptTimelines={{ "attempt-live": timeline }}
        disabled={false}
        onMark={() => {}}
      />,
    );

    expect(screen.getByText(/^6 秒$/)).toBeInTheDocument();
  });

  it("folds completed work and keeps the authoritative final answer outside", () => {
    const timeline = timelineFrom([
      agentEvent(10, "turn_start", {}),
      agentEvent(11, "text", { text: "中间解释" }),
      agentEvent(12, "tool_call", { tool_use_id: "tool-1", tool_name: "Read", input: {} }, "tool-1"),
      agentEvent(13, "tool_result", { tool_use_id: "tool-1", content: "ok" }, "tool-1"),
      agentEvent(14, "text", { text: "最终回答" }),
      agentEvent(15, "finished", {}),
    ]);
    const base = round("published");
    const completed: DiscussionRound = {
      ...base,
      participants: base.participants.map((item, index) =>
        index === 0 ? { ...item, current_attempt_id: "attempt-live" } : item,
      ),
    };

    render(
      <DiscussionRoundTimeline
        participants={participants}
        rounds={[completed]}
        attemptTimelines={{ "attempt-live": timeline }}
        disabled={false}
        onMark={() => {}}
      />,
    );

    expect(screen.getByText("Worked for 10 秒")).toBeInTheDocument();
    expect(screen.getByText("隐藏正文一")).toBeInTheDocument();
  });

  it("folds one successful participant before the rest of the round finishes", () => {
    const timeline = timelineFrom([
      agentEvent(10, "turn_start", {}),
      agentEvent(11, "tool_call", { tool_use_id: "tool-1", tool_name: "Read", input: {} }, "tool-1"),
      agentEvent(12, "tool_result", { tool_use_id: "tool-1", content: "ok" }, "tool-1"),
      agentEvent(13, "text", { text: "先完成者的最终回答" }),
      agentEvent(14, "finished", {}),
    ]);
    const base = round("running");
    const partiallyCompleted: DiscussionRound = {
      ...base,
      participants: base.participants.map((item, index) =>
        index === 0
          ? {
              ...item,
              status: "sealed",
              content: null,
              current_attempt_id: "attempt-live",
              completed_at: "2026-08-06T10:00:10Z",
            }
          : item,
      ),
    };

    render(
      <DiscussionRoundTimeline
        participants={participants}
        rounds={[partiallyCompleted]}
        attemptTimelines={{ "attempt-live": timeline }}
        disabled={false}
        onMark={() => {}}
      />,
    );

    expect(screen.getByText("Worked for 10 秒")).toBeInTheDocument();
    expect(screen.getByText("先完成者的最终回答")).toBeInTheDocument();
    expect(screen.queryByText("隐藏正文一")).toBeNull();
  });

  it("uses the authoritative failure when an empty finished turn is corrected", () => {
    const timeline = timelineFrom([
      agentEvent(10, "turn_start", {}),
      agentEvent(11, "tool_call", { tool_use_id: "tool-1", tool_name: "Read", input: {} }, "tool-1"),
      agentEvent(12, "tool_result", { tool_use_id: "tool-1", content: "ok" }, "tool-1"),
      agentEvent(13, "finished", {}),
      agentEvent(14, "error", {
        subclass: "EMPTY_ANSWER",
        errors: ["参与者已结束，但没有形成可公开的文字回答"],
        api_error_status: null,
      }),
    ]);
    const base = round("running");
    const correctedRound: DiscussionRound = {
      ...base,
      participants: base.participants.map((item, index) =>
        index === 0
          ? {
              ...item,
              status: "sealed",
              content: null,
              current_attempt_id: "attempt-live",
              completed_at: "2026-08-06T10:00:10Z",
            }
          : item,
      ),
    };

    render(
      <DiscussionRoundTimeline
        participants={participants}
        rounds={[correctedRound]}
        attemptTimelines={{ "attempt-live": timeline }}
        disabled={false}
        onMark={() => {}}
      />,
    );

    expect(screen.queryByText(/Worked for/)).toBeNull();
    expect(screen.getAllByText("失败")).toHaveLength(2);
    fireEvent.click(screen.getByRole("button", { name: /展开详情/ }));
    expect(
      screen.getByText("参与者已结束，但没有形成可公开的文字回答"),
    ).toBeInTheDocument();
  });

  it("keeps a failed attempt trajectory visible without a successful Worked fold", () => {
    const timeline = timelineFrom([
      agentEvent(10, "turn_start", {}),
      agentEvent(11, "thinking", { text: "失败前仍在核验" }),
      agentEvent(12, "error", { message: "运行时断开" }),
    ]);
    const base = round("published");
    const failedRound: DiscussionRound = {
      ...base,
      participants: base.participants.map((item, index) =>
        index === 1
          ? { ...item, current_attempt_id: "attempt-live" }
          : item,
      ),
    };

    render(
      <DiscussionRoundTimeline
        participants={participants}
        rounds={[failedRound]}
        attemptTimelines={{ "attempt-live": timeline }}
        disabled={false}
        onMark={() => {}}
      />,
    );

    expect(screen.getByText("连接已断开")).toBeInTheDocument();
    expect(screen.queryByText(/Worked for/)).toBeNull();
    expect(screen.getByRole("button", { name: /Thought/ })).toBeInTheDocument();
  });

  it.each([
    [2, [2]],
    [3, [3]],
    [5, [3, 2]],
    [8, [3, 3, 2]],
  ])("renders %i real cards in balanced rows without changing DOM order", (count, rows) => {
    const dynamicParticipants = Array.from({ length: count }, (_, index) =>
      participant(`p${index + 1}`, index, `参与者 ${index + 1}`),
    );
    const dynamicRound: DiscussionRound = {
      ...round("published"),
      total_participants: count,
      terminal_participants: count,
      participants: dynamicParticipants.map((item) =>
        result(
          item.id,
          item.position,
          item.name,
          "succeeded",
          `正文 ${item.position + 1}`,
          null,
        ),
      ),
    };
    const { container } = render(
      <DiscussionRoundTimeline
        participants={dynamicParticipants}
        rounds={[dynamicRound]}
        disabled={false}
        onMark={() => {}}
      />,
    );

    expect(
      [...container.querySelectorAll(".discussion-card-row")].map(
        (node) => node.children.length,
      ),
    ).toEqual(rows);
    expect(
      [...container.querySelectorAll(".discussion-response")].map((node) =>
        node.getAttribute("data-participant-id"),
      ),
    ).toEqual(dynamicParticipants.map((item) => item.id));
  });

  it("switches the selected full card without reordering mobile segments", () => {
    const { container } = render(
      <DiscussionRoundTimeline
        participants={participants}
        rounds={[round("published")]}
        disabled={false}
        onMark={() => {}}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "核查者" }));

    expect(
      container.querySelector(".discussion-response--selected"),
    ).toHaveAttribute("data-participant-id", "p3");
    expect(
      [...container.querySelectorAll(".discussion-response")].map((node) =>
        node.getAttribute("data-participant-id"),
      ),
    ).toEqual(["p1", "p2", "p3"]);
  });

  it("dims every other participant without hiding or reordering their cards", () => {
    const { container } = render(
      <DiscussionRoundTimeline
        participants={participants}
        rounds={[round("published")]}
        focusedParticipantId="p2"
        disabled={false}
        onMark={() => {}}
      />,
    );

    expect(
      [...container.querySelectorAll(".discussion-response--dimmed")].map((node) =>
        node.getAttribute("data-participant-id"),
      ),
    ).toEqual(["p1", "p3"]);
    expect(container.querySelectorAll(".discussion-response")).toHaveLength(3);
  });
});

function participant(
  id: string,
  position: number,
  name: string,
): DiscussionParticipant {
  return {
    id,
    position,
    name,
    runtime: "codex",
    connection_name: "PRO X20",
    model: "gpt-5.6",
    effective_model: "gpt-5.6",
    effort: "high",
    permission_mode: null,
    permission_preset: "read-only",
    memory_enabled: true,
    profile_enabled: true,
    self_enabled: true,
    status: "active",
  };
}

function timelineFrom(
  events: readonly AgentEvent[],
  runtime: "claude_code" | "codex" = "codex",
): DiscussionAttemptTimeline {
  return events.reduce(
    (timeline, event, index) => applyAttemptEvent(timeline, event, index + 1),
    createLiveAttemptTimeline("attempt-live", "p1", 1, runtime),
  );
}

function agentEvent(
  seq: number,
  type: string,
  payload: Record<string, unknown>,
  itemId: string | null = null,
  runtime: "claude_code" | "codex" = "codex",
): AgentEvent {
  return {
    schema: "agent-event-v1",
    session_id: "session-live",
    runtime,
    seq,
    type,
    thread_id: "thread-live",
    turn_id: "turn-live",
    item_id: itemId,
    payload,
  };
}

function round(status: "running" | "published"): DiscussionRound {
  return {
    id: "round-1",
    number: 1,
    kind: "regular",
    status,
    total_participants: 3,
    terminal_participants: status === "published" ? 3 : 1,
    started_at: "2026-08-06T10:00:00Z",
    published_at: status === "published" ? "2026-08-06T10:00:10Z" : null,
    stop_reason: null,
    participants: [
      result("p1", 0, "分析者", "succeeded", "隐藏正文一", null),
      result("p2", 1, "反方", "host_lost", null, "连接已断开"),
      result("p3", 2, "核查者", "succeeded", "隐藏正文三", null),
    ],
  };
}

function result(
  participantId: string,
  position: number,
  name: string,
  status: string,
  content: string | null,
  errorMessage: string | null,
) {
  return {
    participant_id: participantId,
    current_attempt_id: null,
    position,
    name,
    status,
    content,
    error_code: errorMessage ? "HOST_LOST" : null,
    error_message: errorMessage,
    usage: null,
    activity: {
      tool_call_count: 2,
      tool_names: { Read: 1, WebSearch: 1 },
      subagent_count: 0,
    },
    marked: false,
    started_at: "2026-08-06T10:00:00Z",
    completed_at: "2026-08-06T10:00:10Z",
  };
}
