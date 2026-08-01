/** 冻结 macOS 一级栏、圆角肩部和全局拖动区的结构契约。 */

import { render } from "@testing-library/react";
import { createElement } from "react";
import { describe, expect, it } from "vitest";

import { DESKTOP_LAYOUT_PX } from "../../shared/desktop-layout";
import { AppLayout } from "../components/layout/AppLayout";
import reviewSessionSource from "../components/review/ReviewSession.tsx?raw";

describe("Desktop layout contract", () => {
  it("keeps the left navigation pair at 300px with enough traffic-light clearance", () => {
    expect(DESKTOP_LAYOUT_PX.sidebarWidth).toBe(84);
    expect(DESKTOP_LAYOUT_PX.multiSessionWidth).toBe(216);
    expect(
      DESKTOP_LAYOUT_PX.sidebarWidth + DESKTOP_LAYOUT_PX.multiSessionWidth,
    ).toBe(DESKTOP_LAYOUT_PX.leftColumnsWidth);
    expect(DESKTOP_LAYOUT_PX.trafficLightX).toBe(14);
    expect(DESKTOP_LAYOUT_PX.trafficLightY).toBe(17);
  });

  it("uses a rounded shoulder and leaves the existing full-width short marker intact", () => {
    const { container } = render(
      createElement(
        AppLayout,
        {
          activeTool: "garden",
          onToolChange: () => {},
          sidebarOpen: false,
          onToggleSidebar: () => {},
          children: createElement("div", null, "花园"),
        },
      ),
    );

    expect(DESKTOP_LAYOUT_PX.mainShoulderRadius).toBe(18);
    expect(container.querySelector(".app-main--flush")).toBeNull();
    expect(container.querySelector(".sidebar-nav__item--active")).not.toBeNull();
  });

  it("keeps an explicit draggable top strip mounted while ordinary pages scroll", () => {
    const { container } = render(
      createElement(
        AppLayout,
        {
          activeTool: "garden",
          onToolChange: () => {},
          sidebarOpen: false,
          onToggleSidebar: () => {},
          children: createElement("div", null, "花园"),
        },
      ),
    );

    expect(DESKTOP_LAYOUT_PX.topDragHeight).toBe(48);
    expect(container.querySelector(".app-main__drag-region")).not.toBeNull();
    expect(reviewSessionSource).toContain('className="review-session__drag-region"');
  });
});
