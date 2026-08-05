/** 提供设置详情页共用的标题、状态和空态展示原语。 */

import type { ReactNode } from "react";

interface PanelHeaderProps {
  readonly id: string;
  readonly title: string;
  readonly description: string;
  readonly aside?: ReactNode;
}

/** 统一六组详情页的标题结构。 */
export function PanelHeader({ id, title, description, aside }: PanelHeaderProps) {
  return (
    <header className="settings-panel__header">
      <div>
        <h2 id={id}>{title}</h2>
        <p>{description}</p>
      </div>
      {aside && <span className="settings-panel__aside">{aside}</span>}
    </header>
  );
}

interface StatusPillProps {
  readonly status: string;
  readonly label: string;
}

/** 把可用性保持成文本与颜色双重信号。 */
export function StatusPill({ status, label }: StatusPillProps) {
  return <span className={`settings-status is-${status}`}>{label}</span>;
}

interface EmptyStateProps {
  readonly title: string;
  readonly detail: string;
}

/** 展示明确空态，不把 unknown 冒充成正常。 */
export function EmptyState({ title, detail }: EmptyStateProps) {
  return (
    <div className="settings-empty" role="status">
      <strong>{title}</strong>
      <span>{detail}</span>
    </div>
  );
}
