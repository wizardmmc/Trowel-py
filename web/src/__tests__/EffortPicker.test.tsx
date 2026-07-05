import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { EffortPicker } from "../components/cc/EffortPicker";

describe("EffortPicker (slice-027 C2)", () => {
  it("lists all 6 fixed effort levels", () => {
    render(<EffortPicker currentEffort="medium" onSelect={() => {}} onCancel={() => {}} />);
    for (const v of ["low", "medium", "high", "max", "auto", "ultracode"]) {
      expect(screen.getByText(v)).toBeInTheDocument();
    }
  });

  it("marks the current effort as the initial active option", () => {
    render(<EffortPicker currentEffort="high" onSelect={() => {}} onCancel={() => {}} />);
    const options = screen.getAllByRole("option");
    const highIdx = ["low", "medium", "high", "max", "auto", "ultracode"].indexOf("high");
    expect(options[highIdx]).toHaveAttribute("aria-selected", "true");
  });

  it("ArrowDown + Enter selects the next effort", () => {
    const onSelect = vi.fn();
    render(<EffortPicker currentEffort="medium" onSelect={onSelect} onCancel={() => {}} />);
    const listbox = screen.getByRole("listbox");
    fireEvent.keyDown(listbox, { key: "ArrowDown" }); // medium → high
    fireEvent.keyDown(listbox, { key: "Enter" });
    expect(onSelect).toHaveBeenCalledWith("high");
  });

  it("click an option calls onSelect with the value", () => {
    const onSelect = vi.fn();
    render(<EffortPicker currentEffort="medium" onSelect={onSelect} onCancel={() => {}} />);
    fireEvent.click(screen.getByText("ultracode"));
    expect(onSelect).toHaveBeenCalledWith("ultracode");
  });

  it("flags ultracode as GLM-unverified", () => {
    render(<EffortPicker currentEffort="medium" onSelect={() => {}} onCancel={() => {}} />);
    expect(screen.getByText(/GLM/i)).toBeInTheDocument();
  });

  it("cancel button calls onCancel", () => {
    const onCancel = vi.fn();
    render(<EffortPicker currentEffort="medium" onSelect={() => {}} onCancel={onCancel} />);
    fireEvent.click(screen.getByText("取消"));
    expect(onCancel).toHaveBeenCalled();
  });
});
