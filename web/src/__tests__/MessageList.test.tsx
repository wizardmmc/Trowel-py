import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MessageList } from "../components/cc/MessageList";
import type { Turn } from "../stores/ccStore";

function turn(over: Partial<Turn> = {}): Turn {
  return {
    id: "t1",
    userText: "请回数字 1",
    items: [],
    status: "active",
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
    expect(screen.getByText("思考")).toBeTruthy();
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
    // squash body's top-level children into a kind sequence. Two consecutive
    // text items ("中间"+"段") must merge into one .cc-md block.
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
});
