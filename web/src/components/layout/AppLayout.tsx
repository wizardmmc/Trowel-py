import type { ReactNode } from "react";
import "./AppLayout.css";

export type Tool = "garden" | "extract" | "review";

interface AppLayoutProps {
  readonly children: ReactNode;
  readonly activeTool: Tool;
  readonly onToolChange: (tool: Tool) => void;
  readonly sidebarOpen: boolean;
  readonly onToggleSidebar: () => void;
}

const TOOLS: { id: Tool; icon: string; label: string }[] = [
  { id: "garden", icon: "\u{1F33F}", label: "Garden" },
  { id: "extract", icon: "\u{1F4CB}", label: "Extract" },
  { id: "review", icon: "✅", label: "Review" },
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
        <div className="sidebar-logo">{"\u{1F331}"}</div>
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
      <main className="app-main">
        <button
          className="app-main__hamburger"
          onClick={onToggleSidebar}
          aria-label="Menu"
        >
          {"☰"}
        </button>
        {children}
      </main>
    </div>
  );
}
