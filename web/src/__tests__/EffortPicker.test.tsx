import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { EffortPicker } from "../components/cc/EffortPicker";

describe("EffortPicker", () => {
  it("lists every effort accepted by the installed Claude CLI", () => {
    render(<EffortPicker currentEffort="medium" onSelect={() => {}} onCancel={() => {}} />);
    for (const v of ["low", "medium", "high", "xhigh", "max"]) {
      expect(screen.getByText(v)).toBeInTheDocument();
    }
    expect(screen.queryByText("auto")).toBeNull();
    expect(screen.queryByText("ultracode")).toBeNull();
  });

  it("marks the current effort as the initial active option", () => {
    render(<EffortPicker currentEffort="high" onSelect={() => {}} onCancel={() => {}} />);
    const options = screen.getAllByRole("option");
    const highIdx = ["low", "medium", "high", "xhigh", "max"].indexOf("high");
    expect(options[highIdx]).toHaveAttribute("aria-selected", "true");
  });

  it("ArrowDown + Enter selects the next effort", () => {
    const onSelect = vi.fn();
    render(<EffortPicker currentEffort="medium" onSelect={onSelect} onCancel={() => {}} />);
    const listbox = screen.getByRole("listbox");
    fireEvent.keyDown(listbox, { key: "ArrowDown" });
    fireEvent.keyDown(listbox, { key: "Enter" });
    expect(onSelect).toHaveBeenCalledWith("high");
  });

  it("click an option calls onSelect with the value", () => {
    const onSelect = vi.fn();
    render(<EffortPicker currentEffort="medium" onSelect={onSelect} onCancel={() => {}} />);
    fireEvent.click(screen.getByText("xhigh"));
    expect(onSelect).toHaveBeenCalledWith("xhigh");
  });

  it("cancel button calls onCancel", () => {
    const onCancel = vi.fn();
    render(<EffortPicker currentEffort="medium" onSelect={() => {}} onCancel={onCancel} />);
    fireEvent.click(screen.getByText("取消"));
    expect(onCancel).toHaveBeenCalled();
  });
});
