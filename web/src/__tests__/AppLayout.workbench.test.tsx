import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { AppLayout } from "../components/layout/AppLayout";


describe("AppLayout workbench entry", () => {
  it("keeps Agent and adds workbench as a separate primary destination", async () => {
    const onToolChange = vi.fn();
    render(
      <AppLayout
        activeTool="workbench"
        onToolChange={onToolChange}
        sidebarOpen={false}
        onToggleSidebar={vi.fn()}
      >
        <div>工作台页面</div>
      </AppLayout>,
    );

    expect(screen.getByRole("button", { name: "Agent" })).toBeInTheDocument();
    const workbench = screen.getByRole("button", { name: "工作台" });
    expect(workbench).toHaveClass("sidebar-nav__item--active");
    await userEvent.click(workbench);
    expect(onToolChange).toHaveBeenCalledWith("workbench");
  });
});
