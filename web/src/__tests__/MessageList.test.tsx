import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MessageList } from "../agent/ui";
import {
  getExpectedRuntimePresentation,
  getRuntimePresentation,
} from "../agent/runtimes";
import type { Turn } from "../agent";

function turn(over: Partial<Turn> = {}): Turn {
  return {
    id: "t1",
    userText: "请回数字 1",
    items: [],
    status: "active",
    turnId: null,
    revertible: false,
    ...over,
  };
}

describe("MessageList", () => {
  it("empty state when there are no turns", () => {
    render(<MessageList turns={[]} streaming={false} />);
    expect(screen.getByTestId("cc-empty")).toBeTruthy();
  });

  it("renders user + assistant text as two cards", () => {
    render(
      <MessageList
        turns={[
          turn({
            items: [{ kind: "text", text: "1" }],
            status: "done",
          }),
        ]}
        streaming={false}
      />,
    );
    expect(screen.getByText("请回数字 1")).toBeTruthy();
    expect(screen.getByText("1")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /复制/ })).toBeNull();
  });

  it.each([
    [getExpectedRuntimePresentation("codex"), "Codex"],
    [getExpectedRuntimePresentation("claude_code"), "Claude"],
    [undefined, "Agent"],
  ])("labels assistant turns from its presentation as %s", (presentation, expected) => {
    render(
      <MessageList
        turns={[turn({ items: [{ kind: "text", text: "answer" }] })]}
        streaming={false}
        presentation={presentation}
      />,
    );
    expect(screen.getByText(expected, { selector: ".cc-msg__tag" })).toBeInTheDocument();
  });

  it("renders process events between cards via EventTimeline", () => {
    render(
      <MessageList
        turns={[
          turn({
            items: [
              { kind: "thinking", text: "hmm" },
              { kind: "text", text: "ans" },
            ],
          }),
        ]}
        streaming={true}
      />,
    );
    expect(screen.getByText("Thought")).toBeTruthy();
  });

  it("interleaves text / thinking / tool in item order (B1)", () => {
    const { container } = render(
      <MessageList
        turns={[
          turn({
            items: [
              { kind: "text", text: "开头" },
              { kind: "thinking", text: "想一下" },
              {
                kind: "tool",
                toolUseId: "a",
                toolName: "Bash",
                input: { command: "ls" },
                status: "done",
                elapsedSeconds: 1,
                result: null,
                childTools: [],
              },
              { kind: "text", text: "中间" },
              { kind: "text", text: "段" },
              {
                kind: "tool",
                toolUseId: "b",
                toolName: "Read",
                input: { file_path: "x" },
                status: "done",
                elapsedSeconds: 2,
                result: null,
                childTools: [],
              },
              { kind: "text", text: "结尾" },
            ],
            status: "done",
          }),
        ]}
        streaming={false}
      />,
    );
    const body = container.querySelector(
      ".cc-msg--assistant .cc-msg__body",
    ) as HTMLElement;
    expect(body).toBeTruthy();
    const seq = Array.from(body.children).map((el) => {
      const cls = (el as HTMLElement).className || "";
      if (cls.includes("cc-md")) return "text";
      if (cls.includes("cc-timeline__row--thinking")) return "thinking";
      if (cls.includes("cc-tool")) return "tool";
      return "other";
    });
    expect(seq).toEqual(["text", "thinking", "tool", "text", "tool", "text"]);
  });

  it("error item shows a retry button wired to onRetryLast", () => {
    const onRetry = vi.fn();
    render(
      <MessageList
        turns={[
          turn({
            status: "error",
            items: [
              {
                kind: "error",
                subclass: "error_during_execution",
                errors: ["x"],
                apiErrorStatus: null,
              },
            ],
          }),
        ]}
        streaming={false}
        onRetryLast={onRetry}
      />,
    );
    fireEvent.click(screen.getByText("重试上一条"));
    expect(onRetry).toHaveBeenCalled();
  });

  it("log role is polite and aria-busy reflects streaming", () => {
    const { container } = render(
      <MessageList turns={[turn({ items: [{ kind: "text", text: "x" }] })]} streaming={true} />,
    );
    const log = container.querySelector('[role="log"]') as HTMLElement;
    expect(log).toBeTruthy();
    expect(log.getAttribute("aria-live")).toBe("polite");
    expect(log.getAttribute("aria-busy")).toBe("true");
  });

  it("shows completed turn duration in English", () => {
    render(
      <MessageList
        turns={[
          turn({
            status: "done",
            durationSeconds: 3_900,
          }),
        ]}
        streaming={false}
      />,
    );

    expect(screen.getByLabelText("Ran for 1h 5m")).toHaveTextContent(
      "Ran for 1h 5m",
    );
  });

  it("keeps the currently rendered history when a new turn is appended", () => {
    const turns = [1, 2, 3, 4].map((index) =>
      turn({ id: `t${index}`, userText: `问题 ${index}`, status: "done" }),
    );
    const { rerender } = render(
      <MessageList turns={turns} streaming={false} sticky />,
    );

    expect(screen.queryByText("问题 2")).toBeNull();
    expect(screen.getByText("问题 3")).toBeInTheDocument();
    expect(screen.getByText("问题 4")).toBeInTheDocument();

    rerender(
      <MessageList
        turns={[
          ...turns,
          turn({ id: "t5", userText: "问题 5", status: "active" }),
        ]}
        streaming
        sticky
      />,
    );

    expect(screen.getByText("问题 3")).toBeInTheDocument();
    expect(screen.getByText("问题 4")).toBeInTheDocument();
    expect(screen.getByText("问题 5")).toBeInTheDocument();
  });
});

describe("MessageList — revert button", () => {
  it("shows a revert button for a revertible turn when idle", () => {
    const t = turn({ id: "t1", turnId: "ckpt-1", revertible: true, status: "done" });
    render(<MessageList turns={[t]} streaming={false} />);
    expect(screen.getByTitle("回滚到这轮之前")).toBeTruthy();
  });

  it("hides the revert button when streaming", () => {
    const t = turn({ id: "t1", turnId: "ckpt-1", revertible: true });
    const { container } = render(<MessageList turns={[t]} streaming={true} />);
    expect(container.querySelector(".cc-turn__revert")).toBeNull();
  });

  it("hides the revert button for non-revertible turns (history)", () => {
    const t = turn({ id: "t1", turnId: null, revertible: false });
    const { container } = render(<MessageList turns={[t]} streaming={false} />);
    expect(container.querySelector(".cc-turn__revert")).toBeNull();
  });

  it("hides the revert button when the session did not declare revert", () => {
    const t = turn({ id: "t1", turnId: "ckpt-1", revertible: true, status: "done" });
    const presentation = getRuntimePresentation("claude_code", ["tools"]);
    const { container } = render(
      <MessageList
        turns={[t]}
        streaming={false}
        presentation={presentation}
        onRevert={vi.fn()}
      />,
    );

    expect(container.querySelector(".cc-turn__revert")).toBeNull();
  });

  it("clicking the button calls onRevert with the turn", () => {
    const onRevert = vi.fn();
    const t = turn({ id: "t1", turnId: "ckpt-1", revertible: true, status: "done" });
    render(<MessageList turns={[t]} streaming={false} onRevert={onRevert} />);
    fireEvent.click(screen.getByTitle("回滚到这轮之前"));
    expect(onRevert).toHaveBeenCalledWith(t);
  });
});
