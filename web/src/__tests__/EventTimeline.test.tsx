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

  it("retrying row with attempt=0 shows '重试中' without a number (slice-035 bug3)", () => {
    const items: TurnItem[] = [
      {
        kind: "retrying",
        attempt: 0,
        maxRetries: 5,
        errorStatus: 529,
        error: "overloaded",
        retryDelayMs: 2000,
      },
    ];
    render(<EventTimeline items={items} />);
    expect(screen.getByText(/重试中/)).toBeTruthy();
    expect(screen.queryByText(/重试 0/)).toBeNull();
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

  it("renders text items as an AssistantText markdown block (B2)", () => {
    const items: TurnItem[] = [{ kind: "text", text: "# hello\n\n`code`" }];
    const { container } = render(<EventTimeline items={items} />);
    // slice-025-b: EventTimeline now owns turn-body rendering — text items
    // land here, rendered as markdown (not filtered out).
    expect(container.querySelector(".cc-md")).toBeTruthy();
    expect(container.querySelector("h1")?.textContent).toContain("hello");
    expect(container.querySelector("code")).toBeTruthy();
  });

  it("keeps a paragraph break between adjacent text items (no collapse)", () => {
    // ccStore's text case normally folds same-run deltas into one TextItem,
    // so two adjacent TextItems is a defensive path (two envelopes with no
    // tool between, or a future reducer change). A bare concat would merge
    // them into one paragraph; the join must preserve the boundary.
    const items: TurnItem[] = [
      { kind: "text", text: "para1" },
      { kind: "text", text: "para2" },
    ];
    const { container } = render(<EventTimeline items={items} />);
    const ps = container.querySelectorAll(".cc-md p");
    expect(ps).toHaveLength(2);
    expect(ps[0].textContent).toBe("para1");
    expect(ps[1].textContent).toBe("para2");
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

  it("hides TaskCreate/TaskUpdate/TodoWrite tools (slice-034 feat 5)", () => {
    // 这些工具的语义已在右侧 TodoBar（task_* 事件）体现，对话流不再渲染
    const items: TurnItem[] = [
      { kind: "tool", toolUseId: "1", toolName: "TaskCreate", input: {}, status: "done", elapsedSeconds: 1, result: "ok", childTools: [] },
      { kind: "tool", toolUseId: "2", toolName: "TaskUpdate", input: {}, status: "done", elapsedSeconds: 1, result: "ok", childTools: [] },
      { kind: "tool", toolUseId: "3", toolName: "TodoWrite", input: {}, status: "done", elapsedSeconds: 1, result: "ok", childTools: [] },
    ];
    const { container } = render(<EventTimeline items={items} />);
    expect(container.querySelectorAll(".cc-tool")).toHaveLength(0);
  });

  it("still renders non-task tools (Bash) alongside hidden task tools", () => {
    const items: TurnItem[] = [
      { kind: "tool", toolUseId: "1", toolName: "TaskCreate", input: {}, status: "done", elapsedSeconds: 1, result: "ok", childTools: [] },
      { kind: "tool", toolUseId: "2", toolName: "Bash", input: { command: "ls" }, status: "done", elapsedSeconds: 1, result: "ok", childTools: [] },
    ];
    const { container } = render(<EventTimeline items={items} />);
    expect(container.querySelectorAll(".cc-tool")).toHaveLength(1);
    expect(screen.getByText("Bash")).toBeInTheDocument();
  });
});
