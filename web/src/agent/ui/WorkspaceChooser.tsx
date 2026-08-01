/** 展示所有平台共用的 Recent 工作区和其他文件夹入口。 */

import type { RecentWorkspace } from "../application";

interface WorkspaceChooserProps {
  readonly title: string;
  readonly recents: readonly RecentWorkspace[];
  readonly error?: string | null;
  readonly browseHint?: string;
  readonly onSelect: (path: string) => void;
  readonly onBrowseOther: () => void;
  readonly onCancel: () => void;
}

export function WorkspaceChooser({
  title,
  recents,
  error = null,
  browseHint,
  onSelect,
  onBrowseOther,
  onCancel,
}: WorkspaceChooserProps) {
  return (
    <div className="cc-modal-backdrop" onClick={onCancel}>
      <section
        className="cc-modal cc-workspace-chooser"
        role="dialog"
        aria-label={title}
        onClick={(event) => event.stopPropagation()}
      >
        <header className="cc-modal__head">
          <h2 className="cc-modal__title">{title}</h2>
          <button
            type="button"
            className="cc-workspace-chooser__close"
            onClick={onCancel}
            aria-label="关闭"
          >
            ×
          </button>
        </header>
        <div className="cc-modal__body">
          {error && (
            <div className="cc-workspace-chooser__error" role="alert">
              {error}
            </div>
          )}
          <div className="cc-workspace-chooser__label">最近</div>
          <div className="cc-workspace-chooser__list">
            {recents.length === 0 && (
              <div className="cc-workspace-chooser__empty">暂无最近工作区</div>
            )}
            {recents.map((workspace) => (
              <button
                key={workspace.path}
                type="button"
                className="cc-workspace-chooser__recent"
                onClick={() => onSelect(workspace.path)}
                disabled={!workspace.available}
              >
                <svg viewBox="0 0 24 24" aria-hidden="true">
                  <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
                </svg>
                <span className="cc-workspace-chooser__recent-main">
                  <strong>{workspace.name}</strong>
                  <span>{workspace.path}</span>
                </span>
                {!workspace.available && (
                  <span className="cc-workspace-chooser__unavailable">不可用</span>
                )}
              </button>
            ))}
          </div>
          <button
            type="button"
            className="cc-workspace-chooser__browse"
            onClick={onBrowseOther}
          >
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
              <path d="M12 11v6M9 14h6" />
            </svg>
            打开其他文件夹...
          </button>
          {browseHint && (
            <p className="cc-workspace-chooser__hint">{browseHint}</p>
          )}
        </div>
        <footer className="cc-modal__foot">
          <button type="button" className="cc-btn" onClick={onCancel}>
            取消
          </button>
        </footer>
      </section>
    </div>
  );
}
