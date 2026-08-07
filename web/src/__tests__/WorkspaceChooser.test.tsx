import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { WorkspaceChooser } from "../agent/ui/WorkspaceChooser";

const RECENTS = [
  {
    path: "/workspace/trowel-py",
    name: "trowel-py",
    lastOpenedAt: "2026-08-01T10:00:00+00:00",
    available: true,
  },
  {
    path: "/workspace/missing",
    name: "missing",
    lastOpenedAt: "2026-07-31T10:00:00+00:00",
    available: false,
  },
];

describe("WorkspaceChooser", () => {
  it("selects an available Recent workspace without creating a session", () => {
    const onSelect = vi.fn();
    const onBrowseOther = vi.fn();
    render(
      <WorkspaceChooser
        title="选择工作区"
        recents={RECENTS}
        onSelect={onSelect}
        onBrowseOther={onBrowseOther}
        onCancel={() => {}}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /trowel-py/ }));

    expect(onSelect).toHaveBeenCalledWith("/workspace/trowel-py");
    expect(onBrowseOther).not.toHaveBeenCalled();
  });

  it("keeps unavailable Recent paths visible but disabled", () => {
    render(
      <WorkspaceChooser
        title="选择工作区"
        recents={RECENTS}
        onSelect={() => {}}
        onBrowseOther={() => {}}
        onCancel={() => {}}
      />,
    );

    expect(screen.getByRole("button", { name: /missing/ })).toBeDisabled();
  });

  it("opens the platform-specific folder browser from the shared entry", () => {
    const onBrowseOther = vi.fn();
    render(
      <WorkspaceChooser
        title="选择工作区"
        recents={RECENTS}
        onSelect={() => {}}
        onBrowseOther={onBrowseOther}
        onCancel={() => {}}
      />,
    );

    fireEvent.click(
      screen.getByRole("button", { name: "打开其他文件夹..." }),
    );

    expect(onBrowseOther).toHaveBeenCalledOnce();
  });

  it("uses the nested dialog layer outside the caller stacking context", () => {
    render(
      <div data-testid="caller">
        <WorkspaceChooser
          title="选择工作区"
          recents={RECENTS}
          onSelect={() => {}}
          onBrowseOther={() => {}}
          onCancel={() => {}}
        />
      </div>,
    );

    const dialog = screen.getByRole("dialog", { name: "选择工作区" });
    const backdrop = dialog.closest(".cc-modal-backdrop");
    expect(backdrop).toHaveClass("cc-modal-backdrop--nested");
    expect(screen.getByTestId("caller")).not.toContainElement(dialog);
  });
});
