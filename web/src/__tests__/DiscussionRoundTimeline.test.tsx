/** 验证封闭轮次、共同公开失败态和参与者稳定顺序。 */

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type {
  DiscussionParticipant,
  DiscussionRound,
} from "../discussion/domain";
import { DiscussionRoundTimeline } from "../discussion/ui/DiscussionRoundTimeline";

const participants: readonly DiscussionParticipant[] = [
  participant("p1", 0, "分析者"),
  participant("p2", 1, "反方"),
  participant("p3", 2, "核查者"),
];

describe("DiscussionRoundTimeline", () => {
  it("does not render sealed content before the round is published", () => {
    render(
      <DiscussionRoundTimeline
        participants={participants}
        rounds={[round("running")]}
        disabled={false}
        onMark={() => {}}
      />,
    );

    expect(screen.queryByText("隐藏正文一")).toBeNull();
    expect(screen.getAllByText("正文将在本轮共同公开后显示")).toHaveLength(3);
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
