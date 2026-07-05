import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ModelPicker } from "../components/cc/ModelPicker";
import type { ModelOption } from "../api/cc";

const models: readonly ModelOption[] = [
  { value: "opus", label: "Opus", real_model: "glm-5.2[1M]", description: "最强推理" },
  { value: "sonnet", label: "Sonnet", real_model: "glm-5.1", description: "日常主力" },
];

describe("ModelPicker (slice-027 C2)", () => {
  it("renders each alias with label + real_model + description", () => {
    render(<ModelPicker models={models} currentModel="opus" onSelect={() => {}} onCancel={() => {}} />);
    expect(screen.getByText("Opus")).toBeInTheDocument();
    expect(screen.getByText("glm-5.2[1M]")).toBeInTheDocument();
    expect(screen.getByText("最强推理")).toBeInTheDocument();
  });

  it("marks the current model as the initial active option (aria-selected)", () => {
    render(<ModelPicker models={models} currentModel="sonnet" onSelect={() => {}} onCancel={() => {}} />);
    const options = screen.getAllByRole("option");
    expect(options[1]).toHaveAttribute("aria-selected", "true");
    expect(options[0]).toHaveAttribute("aria-selected", "false");
  });

  it("ArrowDown moves active; Enter confirms the highlighted alias", () => {
    const onSelect = vi.fn();
    render(<ModelPicker models={models} currentModel="opus" onSelect={onSelect} onCancel={() => {}} />);
    const listbox = screen.getByRole("listbox");
    fireEvent.keyDown(listbox, { key: "ArrowDown" }); // opus → sonnet
    fireEvent.keyDown(listbox, { key: "Enter" });
    expect(onSelect).toHaveBeenCalledWith("sonnet");
  });

  it("arrow keys do NOT immediately select (no onChange on navigate)", () => {
    // listbox semantics: arrow moves highlight only; Enter confirms. This is
    // the whole point of switching off native radio (whose arrows fire change).
    const onSelect = vi.fn();
    render(<ModelPicker models={models} currentModel="opus" onSelect={onSelect} onCancel={() => {}} />);
    const listbox = screen.getByRole("listbox");
    fireEvent.keyDown(listbox, { key: "ArrowDown" });
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("click a model calls onSelect with the alias value", () => {
    const onSelect = vi.fn();
    render(<ModelPicker models={models} currentModel="opus" onSelect={onSelect} onCancel={() => {}} />);
    fireEvent.click(screen.getByText("Sonnet"));
    expect(onSelect).toHaveBeenCalledWith("sonnet");
  });

  it("cancel button calls onCancel", () => {
    const onCancel = vi.fn();
    render(<ModelPicker models={models} currentModel="opus" onSelect={() => {}} onCancel={onCancel} />);
    fireEvent.click(screen.getByText("取消"));
    expect(onCancel).toHaveBeenCalled();
  });

  it("shows a title and the lazy-restart hint", () => {
    render(<ModelPicker models={models} currentModel="opus" onSelect={() => {}} onCancel={() => {}} />);
    expect(screen.getByText(/选择模型/)).toBeInTheDocument();
    expect(screen.getByText(/下条消息生效/)).toBeInTheDocument();
  });

  it("Escape closes the picker (a11y)", () => {
    const onCancel = vi.fn();
    render(<ModelPicker models={models} currentModel="opus" onSelect={() => {}} onCancel={onCancel} />);
    fireEvent.keyDown(screen.getByRole("listbox"), { key: "Escape" });
    expect(onCancel).toHaveBeenCalled();
  });

  it("backdrop click cancels", () => {
    const onCancel = vi.fn();
    const { container } = render(
      <ModelPicker models={models} currentModel="opus" onSelect={() => {}} onCancel={onCancel} />,
    );
    fireEvent.click(container.querySelector(".cc-modal-backdrop")!);
    expect(onCancel).toHaveBeenCalled();
  });
});
