import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Composer } from "../components/cc/Composer";

describe("Composer memory/profile chips", () => {
  it("renders read-only chips reflecting the active session's condition", () => {
    const { container } = render(
      <Composer
        streaming={false}
        disabled={false}
        onSend={() => {}}
        onInterrupt={() => {}}
        memoryEnabled={false}
        profileEnabled={true}
      />,
    );
    expect(container.querySelector(".cc-cond--off")).not.toBeNull();
    expect(container.querySelector(".cc-cond--on")).not.toBeNull();
  });

  it("renders no chips when the condition is unknown (no active session)", () => {
    const { container } = render(
      <Composer streaming={false} disabled={false} onSend={() => {}} onInterrupt={() => {}} />,
    );
    expect(container.querySelector(".cc-cond")).toBeNull();
  });
});

describe("Composer Esc three-state", () => {
  it("Enter sends and clears the input", () => {
    const onSend = vi.fn();
    render(<Composer streaming={false} disabled={false} onSend={onSend} onInterrupt={() => {}} />);
    const ta = screen.getByLabelText("CC 消息输入") as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: "hi" } });
    fireEvent.keyDown(ta, { key: "Enter" });
    expect(onSend).toHaveBeenCalledWith("hi");
    expect(ta.value).toBe("");
  });

  it("Shift+Enter does NOT send (newline passthrough)", () => {
    const onSend = vi.fn();
    render(<Composer streaming={false} disabled={false} onSend={onSend} onInterrupt={() => {}} />);
    const ta = screen.getByLabelText("CC 消息输入");
    fireEvent.change(ta, { target: { value: "hi" } });
    fireEvent.keyDown(ta, { key: "Enter", shiftKey: true });
    expect(onSend).not.toHaveBeenCalled();
  });

  it("Esc clears input when there is text (most-specific first)", () => {
    const onInterrupt = vi.fn();
    render(<Composer streaming={true} disabled={false} onSend={() => {}} onInterrupt={onInterrupt} />);
    const ta = screen.getByLabelText("CC 消息输入") as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: "x" } });
    fireEvent.keyDown(ta, { key: "Escape" });
    expect(ta.value).toBe("");
    expect(onInterrupt).not.toHaveBeenCalled();
  });

  it("Esc interrupts when input is empty and streaming", () => {
    const onInterrupt = vi.fn();
    render(<Composer streaming={true} disabled={false} onSend={() => {}} onInterrupt={onInterrupt} />);
    fireEvent.keyDown(screen.getByLabelText("CC 消息输入"), { key: "Escape" });
    expect(onInterrupt).toHaveBeenCalled();
  });

  it("Esc does nothing when idle and input empty", () => {
    const onInterrupt = vi.fn();
    render(<Composer streaming={false} disabled={false} onSend={() => {}} onInterrupt={onInterrupt} />);
    fireEvent.keyDown(screen.getByLabelText("CC 消息输入"), { key: "Escape" });
    expect(onInterrupt).not.toHaveBeenCalled();
  });

  it("disabled composer blocks send", () => {
    const onSend = vi.fn();
    render(<Composer streaming={false} disabled={true} onSend={onSend} onInterrupt={() => {}} />);
    const ta = screen.getByLabelText("CC 消息输入") as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: "hi" } });
    fireEvent.keyDown(ta, { key: "Enter" });
    expect(onSend).not.toHaveBeenCalled();
  });
});
