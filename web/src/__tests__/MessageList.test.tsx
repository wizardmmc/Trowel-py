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
