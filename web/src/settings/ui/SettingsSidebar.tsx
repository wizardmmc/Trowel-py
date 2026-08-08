/** 展示设置页固定二级导航，不拥有页面状态。 */

import type { SettingsSection } from "../domain/types";
import { SETTINGS_SECTIONS } from "./sectionMetadata";

interface SettingsSidebarProps {
  readonly activeSection: SettingsSection;
  readonly status: "loading" | "ready" | "error";
  readonly onSectionChange: (section: SettingsSection) => void;
}

const SECTION_ICON_PATHS: Readonly<Record<SettingsSection, string>> = {
  paths: "M3.5 7.5h6l1.6 2h9.4v8.5a2 2 0 0 1-2 2h-15a2 2 0 0 1-2-2V9.5a2 2 0 0 1 2-2Z",
  connections: "M5 5.5h14v5H5zM5 13.5h14v5H5zM8 8h.01M8 16h.01",
  configurations: "M5 5.5h14v5H5zM5 13.5h14v5H5zM8 8h.01M8 16h.01M11 8h5M11 16h5",
  tasks: "m12 3 1.5 4.2L18 8.5l-3.5 2.7.2 4.5L12 13.2 9.3 15.7l.2-4.5L6 8.5l4.5-1.3Z",
  agent: "M4 7h16M7 7v10M17 7v10M4 17h16M10 11h4",
  diagnostics: "M4 12h3l2-5 4 10 2-5h5",
  about: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18ZM12 10v6M12 7h.01",
};

function SettingsSectionIcon({ section }: { readonly section: SettingsSection }) {
  return (
    <svg className="settings-sidebar__icon" viewBox="0 0 24 24" aria-hidden="true">
      <path d={SECTION_ICON_PATHS[section]} />
    </svg>
  );
}

/** 渲染固定宽度的六组设置入口。 */
export function SettingsSidebar({
  activeSection,
  status,
  onSectionChange,
}: SettingsSidebarProps) {
  const statusLabel = {
    loading: "正在读取本机配置",
    ready: "本机配置",
    error: "配置读取失败",
  }[status];
  return (
    <aside className="settings-sidebar" aria-label="设置分类">
      <header className="settings-sidebar__header">
        <strong>设置</strong>
      </header>
      <div className="settings-sidebar__summary">
        <span className={`settings-sidebar__status-dot is-${status}`} aria-hidden="true" />
        {statusLabel}
      </div>
      <nav className="settings-sidebar__nav">
        {(["configuration", "system"] as const).map((group) => (
          <section className="settings-sidebar__group" key={group}>
            <p>{group === "configuration" ? "配置" : "系统"}</p>
            {SETTINGS_SECTIONS.filter((section) => section.group === group).map((section) => (
              <button
                key={section.id}
                type="button"
                title={section.detail}
                className={`settings-sidebar__item${activeSection === section.id ? " is-active" : ""}`}
                onClick={() => onSectionChange(section.id)}
              >
                <SettingsSectionIcon section={section.id} />
                <strong>{section.label}</strong>
              </button>
            ))}
          </section>
        ))}
      </nav>
      <footer className="settings-sidebar__note">保存只影响之后创建的任务和会话</footer>
    </aside>
  );
}
