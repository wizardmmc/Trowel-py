/** 提供全局侧边栏、工具导航和主内容布局。 */

import type { CSSProperties, ReactNode } from "react";
import { DESKTOP_LAYOUT_PX } from "../../../shared/desktop-layout";
import "./AppLayout.css";

export type Tool =
  | "garden"
  | "extract"
  | "review"
  | "cc"
  | "discussion"
  | "statistics"
  | "profile"
  | "settings";

interface AppLayoutProps {
  readonly children: ReactNode;
  readonly activeTool: Tool;
  readonly onToolChange: (tool: Tool) => void;
  readonly sidebarOpen: boolean;
  readonly onToggleSidebar: () => void;
  readonly inspectionOnly?: boolean;
}

function IconGarden() {
  return (
    <svg className="sidebar-nav__svg" viewBox="0 0 24 24" aria-hidden="true">
      <path d="M5 19c0-7 5-12 14-13 0 9-5 14-13 14" />
      <path d="M5 19c2-1 4-1 6 0" />
      <path d="M12 19v-6" />
    </svg>
  );
}

function IconExtract() {
  return (
    <svg className="sidebar-nav__svg" viewBox="0 0 24 24" aria-hidden="true">
      <rect x="6" y="4" width="12" height="17" rx="2" />
      <path d="M9 4v-.5A1.5 1.5 0 0 1 10.5 2h3A1.5 1.5 0 0 1 15 3.5V4" />
      <path d="M9 11h6M9 15h4" />
    </svg>
  );
}

function IconReview() {
  return (
    <svg className="sidebar-nav__svg" viewBox="0 0 24 24" aria-hidden="true">
      <path d="M5 13l4 4L19 7" />
    </svg>
  );
}

function IconAgent() {
  return (
    <svg className="sidebar-nav__svg" viewBox="0 0 24 24" aria-hidden="true">
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <path d="M7 9l3 3-3 3" />
      <path d="M13 15h4" />
    </svg>
  );
}

function IconDiscussion() {
  return (
    <svg className="sidebar-nav__svg" viewBox="0 0 24 24" aria-hidden="true">
      <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" />
      <circle cx="9" cy="7" r="4" />
      <path d="M22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75" />
    </svg>
  );
}

function IconStatistics() {
  return (
    <svg className="sidebar-nav__svg" viewBox="0 0 24 24" aria-hidden="true">
      <path d="M4 20V10M10 20V4M16 20v-7M22 20H2" />
    </svg>
  );
}

function IconProfile() {
  return (
    <svg className="sidebar-nav__svg" viewBox="0 0 24 24" aria-hidden="true">
      <circle cx="12" cy="8" r="4" />
      <path d="M4 21c0-4 4-7 8-7s8 3 8 7" />
    </svg>
  );
}

function IconSettings() {
  return (
    <svg className="sidebar-nav__svg" viewBox="0 0 24 24" aria-hidden="true">
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2.8 2.8-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6v.2h-4V21a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1L4.2 17l.1-.1a1.7 1.7 0 0 0 .3-1.9A1.7 1.7 0 0 0 3 14H2.8v-4H3a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9L4.2 7 7 4.2l.1.1A1.7 1.7 0 0 0 9 4.6 1.7 1.7 0 0 0 10 3v-.2h4V3a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 1.9-.3l.1-.1L19.8 7l-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.6 1h.2v4H21a1.7 1.7 0 0 0-1.6 1Z" />
    </svg>
  );
}

const TOOLS: { id: Tool; icon: ReactNode; label: string }[] = [
  { id: "garden", icon: <IconGarden />, label: "花园" },
  { id: "extract", icon: <IconExtract />, label: "提取" },
  { id: "review", icon: <IconReview />, label: "复习" },
  { id: "cc", icon: <IconAgent />, label: "Agent" },
  { id: "discussion", icon: <IconDiscussion />, label: "研讨" },
  { id: "statistics", icon: <IconStatistics />, label: "统计" },
  { id: "profile", icon: <IconProfile />, label: "画像" },
];

const SETTINGS_TOOL = { id: "settings" as const, icon: <IconSettings />, label: "设置" };

export function AppLayout({
  children,
  activeTool,
  onToolChange,
  sidebarOpen,
  onToggleSidebar,
  inspectionOnly = false,
}: AppLayoutProps) {
  const layoutStyle = {
    "--app-sidebar-width": `${DESKTOP_LAYOUT_PX.sidebarWidth}px`,
    "--agent-multi-width": `${DESKTOP_LAYOUT_PX.multiSessionWidth}px`,
    "--app-main-shoulder": `${DESKTOP_LAYOUT_PX.mainShoulderRadius}px`,
    "--app-top-drag-height": `${DESKTOP_LAYOUT_PX.topDragHeight}px`,
  } as CSSProperties;

  return (
    <div className="app-layout" style={layoutStyle}>
      <aside
        className={`app-sidebar ${sidebarOpen ? "app-sidebar--open" : ""}`}
      >
        <div className="sidebar-logo" aria-hidden="true">
          <img
            className="sidebar-logo__svg"
            src="./brand/trowel-mark.svg"
            alt=""
          />
        </div>
        <nav className="sidebar-nav">
          {TOOLS.filter((tool) => !inspectionOnly || tool.id === "statistics").map((tool) => (
            <button
              key={tool.id}
              className={`sidebar-nav__item ${activeTool === tool.id ? "sidebar-nav__item--active" : ""}`}
              onClick={() => onToolChange(tool.id)}
              aria-label={tool.label}
              title={tool.label}
            >
              <span className="sidebar-nav__icon">{tool.icon}</span>
              <span className="sidebar-nav__label">{tool.label}</span>
            </button>
          ))}
        </nav>
        {!inspectionOnly && (
          <nav className="sidebar-nav sidebar-nav--utility" aria-label="应用设置">
            <button
              className={`sidebar-nav__item ${activeTool === SETTINGS_TOOL.id ? "sidebar-nav__item--active" : ""}`}
              onClick={() => onToolChange(SETTINGS_TOOL.id)}
              aria-label={SETTINGS_TOOL.label}
              title={SETTINGS_TOOL.label}
            >
              <span className="sidebar-nav__icon">{SETTINGS_TOOL.icon}</span>
              <span className="sidebar-nav__label">{SETTINGS_TOOL.label}</span>
            </button>
          </nav>
        )}
      </aside>
      <main
        className={`app-main${activeTool === "cc" || activeTool === "discussion" || activeTool === "settings" ? " app-main--flush" : ""}${activeTool === "statistics" ? " app-main--statistics" : ""}`}
      >
        {activeTool !== "cc" && activeTool !== "discussion" && activeTool !== "settings" && (
          <div className="app-main__drag-region" aria-hidden="true" />
        )}
        <button
          className="app-main__hamburger"
          onClick={onToggleSidebar}
          aria-label="菜单"
        >
          <svg
            className="app-main__hamburger-svg"
            viewBox="0 0 24 24"
            aria-hidden="true"
          >
            <path d="M4 6h16M4 12h16M4 18h16" />
          </svg>
        </button>
        {children}
      </main>
    </div>
  );
}
