import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { CodexCommandDialogs } from "../components/cc/CodexCommandDialogs";
import { createNewSessionState } from "../stores/ccStore/sessionState";

function active() {
  const session = createNewSessionState(
    {
      session_id: "s1",
      runtime: "codex",
      native_session_id: "thread-1",
      workdir: "/repo",
      model: "gpt-5.6-sol",
      effort: "high",
      permission: "Workspace write · on-request",
      effective_sandbox: "workspace-write",
      effective_approval: "on-request",
      network_access: null,
      memory_enabled: true,
      profile_enabled: true,
      capabilities: ["tools", "approval"],
      name: "repo",
      connected: true,
      running: false,
    },
    { workdir: "/repo", runtime: "codex", effort: "high" },
  );
  return {
    ...session,
    meta: {
      ...session.meta,
      usage: {
        total: { totalTokens: 15495 },
        last: null,
        model_context_window: 258400,
      },
      rateLimit: {
        limit_id: "codex",
        limit_name: null,
        primary: { usedPercent: 20, windowDurationMins: 300, resetsAt: null },
        secondary: null,
        credits: null,
        individual_limit: null,
        spend_control_reached: null,
        plan_type: "pro",
        rate_limit_reached_type: null,
      },
    },
  };
}

describe("CodexCommandDialogs", () => {
  it("renders real status facts and labels missing values explicitly", () => {
    render(
      <CodexCommandDialogs
        kind="status"
        active={active()}
        onClose={() => {}}
        onStartReview={() => {}}
        reviewPending={false}
        reviewError={null}
      />,
    );

    expect(screen.getByRole("dialog", { name: "Codex 会话状态" })).toBeInTheDocument();
    expect(screen.getByText("15,495 / 258,400")).toBeInTheDocument();
    expect(screen.getByText("20%")).toBeInTheDocument();
    expect(screen.getByText("未提供")).toBeInTheDocument();
  });

  it("validates and submits each review target shape", () => {
    const onStart = vi.fn();
    render(
      <CodexCommandDialogs
        kind="review"
        active={active()}
        onClose={() => {}}
        onStartReview={onStart}
        reviewPending={false}
        reviewError={null}
      />,
    );

    fireEvent.click(screen.getByRole("radio", { name: /相对基线分支/ }));
    const start = screen.getByRole("button", { name: "开始审查" });
    expect(start).toBeDisabled();
    fireEvent.change(screen.getByLabelText("基线分支"), {
      target: { value: "main" },
    });
    fireEvent.click(start);
    expect(onStart).toHaveBeenCalledWith({ type: "baseBranch", branch: "main" });
  });

  it("renders an empty diff state without inventing working-tree output", () => {
    render(
      <CodexCommandDialogs
        kind="diff"
        active={active()}
        onClose={() => {}}
        onStartReview={() => {}}
        reviewPending={false}
        reviewError={null}
      />,
    );

    expect(screen.getByText("当前 turn 还没有聚合 diff")).toBeInTheDocument();
    expect(screen.queryByText(/diff --git/)).not.toBeInTheDocument();
  });

  it("closes on Escape", () => {
    const onClose = vi.fn();
    render(
      <CodexCommandDialogs
        kind="status"
        active={active()}
        onClose={onClose}
        onStartReview={() => {}}
        reviewPending={false}
        reviewError={null}
      />,
    );
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
