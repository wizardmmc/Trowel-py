/** 验证发送对象、右栏强调和讨论状态共用真实参与者与轮次事实。 */

import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { Discussion } from "../discussion/domain";
import { DiscussionView } from "../discussion/ui/DiscussionView";

const discussion: Discussion = {
  id: "discussion-focus",
  topic: "如何验证讨论页面的强调交互？",
  workdir: "/repo",
  progression_mode: "automatic",
  max_rounds: 4,
  status: "waiting_user",
  version: 3,
  active_round_number: 1,
  created_at: "2026-08-07T10:00:00Z",
  updated_at: "2026-08-07T10:01:00Z",
  completed_at: null,
  stopped_at: null,
  participants: [
    participant("p1", 0, "参与者 1", "claude_code", "acceptEdits", null),
    participant("p2", 1, "参与者 2", "codex", null, "workspace-write"),
    participant("p3", 2, "参与者 3", "codex", null, "read-only"),
  ],
  messages: [],
  rounds: [
    {
      id: "round-1",
      number: 1,
      kind: "regular",
      status: "published",
      total_participants: 3,
      terminal_participants: 3,
      started_at: "2026-08-07T10:00:00Z",
      published_at: "2026-08-07T10:01:00Z",
      stop_reason: null,
      participants: [
        result("p1", 0, "参与者 1", 1200, true),
        result("p2", 1, "参与者 2", 2300, false),
        result("p3", 2, "参与者 3", 3500, false),
      ],
    },
  ],
  handoffs: [],
};

describe("DiscussionView focus", () => {
  it("uses send targets to focus cards and all to clear the focus", () => {
    const { container } = renderView();
    const targets = screen.getByLabelText("补充对象");

    fireEvent.click(within(targets).getByRole("button", { name: "参与者 2" }));
    expect(dimmedIds(container)).toEqual(["p1", "p3"]);

    fireEvent.click(within(targets).getByRole("button", { name: "全体" }));
    expect(dimmedIds(container)).toEqual([]);
  });

  it("toggles right-rail focus without changing the selected send target", () => {
    const { container } = renderView();
    const inspector = screen.getByRole("complementary", { name: "参与者状态" });
    const focusButton = within(inspector).getByRole("button", { name: "强调 参与者 1" });

    fireEvent.click(focusButton);
    expect(focusButton).toHaveAttribute("aria-pressed", "true");
    expect(dimmedIds(container)).toEqual(["p2", "p3"]);
    expect(within(screen.getByLabelText("补充对象")).getByRole("button", { name: "全体" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );

    fireEvent.click(focusButton);
    expect(focusButton).toHaveAttribute("aria-pressed", "false");
    expect(dimmedIds(container)).toEqual([]);
  });

  it("renders the mockup memory boundary and facts from the discussion snapshot", () => {
    renderView();
    const inspector = screen.getByRole("complementary", { name: "参与者状态" });

    expect(within(inspector).getByText("Memory 资格")).toBeInTheDocument();
    expect(within(inspector).getByText("Daily / Episode")).toBeInTheDocument();
    expect(within(inspector).getByText("默认关闭")).toBeInTheDocument();
    expect(within(inspector).getByText("讨论状态")).toBeInTheDocument();
    expect(within(inspector).getByText("1 / 4")).toBeInTheDocument();
    expect(within(inspector).getAllByText(/PRO X20|DeepSeek/).length).toBeGreaterThan(0);
    expect(within(inspector).getByText("3 个原生会话")).toBeInTheDocument();
    expect(within(inspector).getByText("约 7.0k token")).toBeInTheDocument();
    expect(within(inspector).getByText("1 条")).toBeInTheDocument();
    expect(within(inspector).getByText("repo")).toHaveAttribute(
      "title",
      "/repo",
    );
  });
});

function renderView() {
  return render(
    <DiscussionView
      discussion={discussion}
      pendingCommand={null}
      error={null}
      onStart={() => {}}
      onContinue={() => {}}
      onFinish={() => {}}
      onStop={() => {}}
      onResume={() => {}}
      onDelete={() => {}}
      onAddMessage={async () => {}}
      onMark={() => {}}
      onHandoff={() => {}}
      onClearError={() => {}}
    />,
  );
}

function dimmedIds(container: HTMLElement): string[] {
  return [...container.querySelectorAll(".discussion-response--dimmed")].map(
    (node) => node.getAttribute("data-participant-id") ?? "",
  );
}

function participant(
  id: string,
  position: number,
  name: string,
  runtime: "claude_code" | "codex",
  permissionMode: string | null,
  permissionPreset: "read-only" | "workspace-write" | null,
) {
  return {
    id,
    position,
    name,
    runtime,
    connection_name: runtime === "codex" ? "PRO X20" : "DeepSeek",
    model: runtime === "codex" ? "gpt-test" : "sonnet",
    effective_model:
      runtime === "codex" ? "gpt-test" : "deepseek-v4-flash",
    effort: "medium",
    permission_mode: permissionMode,
    permission_preset: permissionPreset,
    memory_enabled: true,
    profile_enabled: true,
    self_enabled: true,
    status: "ready",
  } as const;
}

function result(
  participantId: string,
  position: number,
  name: string,
  totalTokens: number,
  marked: boolean,
) {
  return {
    participant_id: participantId,
    position,
    name,
    status: "succeeded",
    content: `${name} 的完整正文`,
    error_code: null,
    error_message: null,
    usage: { total_tokens: totalTokens },
    activity: {
      tool_call_count: 1,
      tool_names: { Read: 1 },
      subagent_count: 0,
    },
    marked,
    started_at: "2026-08-07T10:00:00Z",
    completed_at: "2026-08-07T10:01:00Z",
  } as const;
}
