import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { EventTimeline } from "../components/cc/EventTimeline";
import type { TurnItem } from "../stores/ccStore";

describe("EventTimeline", () => {
  it("thinking row shows a summary and expands to the raw text", () => {
    const items: TurnItem[] = [{ kind: "thinking", text: "reasoning here" }];
    render(<EventTimeline items={items} />);
    expect(screen.getByText("思考")).toBeTruthy();
    fireEvent.click(screen.getByRole("button"));
    expect(screen.getByText("reasoning here")).toBeTruthy();
  });

  it("retrying row surfaces attempt + GLM status + delay", () => {
    const items: TurnItem[] = [
      {
        kind: "retrying",
        attempt: 1,
        maxRetries: 5,
        errorStatus: 529,
        error: "overloaded",
        retryDelayMs: 2000,
      },
    ];
    render(<EventTimeline items={items} />);
    expect(screen.getByText(/重试 1\/5/)).toBeTruthy();
    expect(screen.getByText(/GLM 529/)).toBeTruthy();
    expect(screen.getByText(/2s 后/)).toBeTruthy();
  });

  it("error row shows a retry button only for recoverable subclass", () => {
    const recoverable: TurnItem[] = [
      {
        kind: "error",
        subclass: "error_during_execution",
        errors: ["boom"],
        apiErrorStatus: null,
      },
    ];
    const onRetry = vi.fn();
    const { rerender } = render(<EventTimeline items={recoverable} onRetryLast={onRetry} />);
    expect(screen.getByText("重试上一条")).toBeTruthy();
    fireEvent.click(screen.getByText("重试上一条"));
    expect(onRetry).toHaveBeenCalled();

    const terminal: TurnItem[] = [
      {
        kind: "error",
        subclass: "error_max_turns",
        errors: ["loop"],
        apiErrorStatus: null,
      },
    ];
    rerender(<EventTimeline items={terminal} onRetryLast={onRetry} />);
    expect(screen.queryByText("重试上一条")).toBeNull();
    expect(screen.getByText("error_max_turns")).toBeTruthy();
  });

  it("interrupted row shows the soft-transition guidance", () => {
    const items: TurnItem[] = [{ kind: "interrupted" }];
    render(<EventTimeline items={items} />);
    expect(screen.getByText(/已中断/)).toBeTruthy();
    expect(screen.getByText(/自动接上历史/)).toBeTruthy();
  });

  it("compact_boundary renders a divider", () => {
    const items: TurnItem[] = [{ kind: "compact_boundary" }];
    render(<EventTimeline items={items} />);
    expect(screen.getByText(/自动压缩完成/)).toBeTruthy();
  });

  it("text items are not rendered here (handled by MessageList)", () => {
    const items: TurnItem[] = [{ kind: "text", text: "hello" }];
    const { container } = render(<EventTimeline items={items} />);
    expect(container.querySelector(".cc-timeline")).toBeNull();
  });

  it("Agent tool renders SubagentBlock with the childTools region (阶段B)", () => {
    const items: TurnItem[] = [
      {
        kind: "tool",
        toolUseId: "agent-1",
        toolName: "Agent",
        input: {},
        status: "running",
        elapsedSeconds: null,
        result: null,
        childTools: [
          {
            kind: "tool",
            toolUseId: "child-bash",
            toolName: "Bash",
            input: {},
            status: "done",
            elapsedSeconds: 3,
            result: "ok",
            childTools: [],
          },
        ],
      },
    ];
    render(<EventTimeline items={items} />);
    expect(screen.getByText(/Agent/)).toBeInTheDocument();
    expect(screen.getByText(/Bash/)).toBeInTheDocument();
  });
});
