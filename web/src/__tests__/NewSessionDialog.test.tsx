import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

import { NewSessionDialog } from "../components/cc/NewSessionDialog";

describe("NewSessionDialog (slice-060)", () => {
  it("defaults both switches ON (zero-regression)", () => {
    render(
      <NewSessionDialog workdir="/wd" onCreate={() => {}} onCancel={() => {}} />,
    );
    const switches = screen.getAllByRole("switch");
    expect(switches).toHaveLength(2);
    expect(switches[0]).toHaveAttribute("aria-checked", "true"); // Memory
    expect(switches[1]).toHaveAttribute("aria-checked", "true"); // Profile
  });

  it("toggling Memory flips only its switch (the two are independent)", () => {
    render(
      <NewSessionDialog workdir="/wd" onCreate={() => {}} onCancel={() => {}} />,
    );
    fireEvent.click(screen.getAllByRole("switch")[0]); // Memory off
    const switches = screen.getAllByRole("switch");
    expect(switches[0]).toHaveAttribute("aria-checked", "false");
    expect(switches[1]).toHaveAttribute("aria-checked", "true"); // Profile unchanged
  });

  it("创建会话 fires onCreate with the chosen switches", () => {
    const onCreate = vi.fn();
    render(
      <NewSessionDialog workdir="/wd" onCreate={onCreate} onCancel={() => {}} />,
    );
    fireEvent.click(screen.getAllByRole("switch")[0]); // Memory off → off/on combo
    fireEvent.click(screen.getByText("创建会话"));
    expect(onCreate).toHaveBeenCalledWith(false, true);
  });

  it("取消 fires onCancel and does NOT create", () => {
    const onCreate = vi.fn();
    const onCancel = vi.fn();
    render(
      <NewSessionDialog workdir="/wd" onCreate={onCreate} onCancel={onCancel} />,
    );
    fireEvent.click(screen.getByText("取消"));
    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(onCreate).not.toHaveBeenCalled();
  });

  it("clicking the backdrop cancels (no session created)", () => {
    const onCreate = vi.fn();
    const onCancel = vi.fn();
    render(
      <NewSessionDialog workdir="/wd" onCreate={onCreate} onCancel={onCancel} />,
    );
    fireEvent.click(document.querySelector(".cc-dialog__backdrop") as Element);
    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(onCreate).not.toHaveBeenCalled();
  });

  it("Esc closes the dialog (cancels)", () => {
    const onCreate = vi.fn();
    const onCancel = vi.fn();
    render(
      <NewSessionDialog workdir="/wd" onCreate={onCreate} onCancel={onCancel} />,
    );
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(onCreate).not.toHaveBeenCalled();
  });
});
