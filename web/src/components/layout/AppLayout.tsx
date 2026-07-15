import type { ReactNode } from "react";
import "./AppLayout.css";

export type Tool = "garden" | "extract" | "review" | "cc" | "profile";

interface AppLayoutProps {
  readonly children: ReactNode;
  readonly activeTool: Tool;
  readonly onToolChange: (tool: Tool) => void;
  readonly sidebarOpen: boolean;
  readonly onToggleSidebar: () => void;
}

// 线条 SVG 图标（slice021-web：替代原 emoji 🌿📋✅🌱，跨平台渲染一致）
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

function IconSprout() {
  return (
    <svg className="sidebar-logo__svg" viewBox="0 0 24 24" aria-hidden="true">
      <path d="M12 21v-8" />
      <path d="M12 13c0-4-3-6-7-6 0 4 3 6 7 6z" />
      <path d="M12 11c0-3 2.5-5 6-5 0 3-2.5 5-6 5z" />
    </svg>
  );
}

function IconCC() {
  // terminal-style prompt — CC is the "shell out to Claude Code" tool
  return (
    <svg className="sidebar-nav__svg" viewBox="0 0 24 24" aria-hidden="true">
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <path d="M7 9l3 3-3 3" />
      <path d="M13 15h4" />
    </svg>
  );
}

function IconProfile() {
  // person silhouette — the "who you are" self-description tab (slice-049)
  return (
    <svg className="sidebar-nav__svg" viewBox="0 0 24 24" aria-hidden="true">
      <circle cx="12" cy="8" r="4" />
      <path d="M4 21c0-4 4-7 8-7s8 3 8 7" />
    </svg>
  );
}

const TOOLS: { id: Tool; icon: ReactNode; label: string }[] = [
  { id: "garden", icon: <IconGarden />, label: "花园" },
  { id: "extract", icon: <IconExtract />, label: "提取" },
  { id: "review", icon: <IconReview />, label: "复习" },
  { id: "cc", icon: <IconCC />, label: "CC" },
  { id: "profile", icon: <IconProfile />, label: "画像" },
];

export function AppLayout({
  children,
  activeTool,
  onToolChange,
  sidebarOpen,
  onToggleSidebar,
}: AppLayoutProps) {
  return (
    <div className="app-layout">
      <aside
        className={`app-sidebar ${sidebarOpen ? "app-sidebar--open" : ""}`}
      >
        <div className="sidebar-logo">
          <IconSprout />
        </div>
        <nav className="sidebar-nav">
          {TOOLS.map((tool) => (
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
      </aside>
      <main className={`app-main${activeTool === "cc" ? " app-main--flush" : ""}`}>
        <button
          className="app-main__hamburger"
          onClick={onToggleSidebar}
          aria-label="菜单"
        >
          <svg className="app-main__hamburger-svg" viewBox="0 0 24 24" aria-hidden="true">
            <path d="M4 6h16M4 12h16M4 18h16" />
          </svg>
        </button>
        {children}
      </main>
    </div>
  );
}
