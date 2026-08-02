/** 展示 Agent 首次进入时的 Start 与 Recent 工作区。 */

import type { RecentWorkspace } from "../application";

interface WorkspaceStartProps {
  readonly recents: readonly RecentWorkspace[];
  readonly loading: boolean;
  readonly error: string | null;
  readonly onOpenWorkspace: () => void;
  readonly onNewSession: () => void;
  readonly onOpenHistory: () => void;
  readonly onSelectRecent: (path: string) => void;
  readonly onRetry: () => void;
}

/** 把 Recent 的 UTC 时间转换成首页使用的紧凑说明。 */
function formatRecentTime(value: string, now = new Date()): string {
  const openedAt = new Date(value);
  const elapsed = now.getTime() - openedAt.getTime();
  if (!Number.isFinite(elapsed) || elapsed < 0) return "";
  const minutes = Math.floor(elapsed / 60_000);
  if (minutes < 1) return "刚刚";
  if (minutes < 60) return `${minutes} 分钟前`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} 小时前`;
  if (hours < 48) return "昨天";
  return `${openedAt.getMonth() + 1} 月 ${openedAt.getDate()} 日`;
}

/** 渲染已确认的 VS Code 式 Agent 初始页。 */
export function WorkspaceStart({
  recents,
  loading,
  error,
  onOpenWorkspace,
  onNewSession,
  onOpenHistory,
  onSelectRecent,
  onRetry,
}: WorkspaceStartProps) {
  return (
    <section className="cc-workspace-start" aria-label="Agent 开始页">
      <div className="cc-workspace-start__inner">
        <header className="cc-workspace-start__brand">
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M12 21v-8" />
            <path d="M12 13c0-4-3-6-7-6 0 4 3 6 7 6z" />
            <path d="M12 11c0-3 2.5-5 6-5 0 3-2.5 5-6 5z" />
          </svg>
          <div>
            <h1>Trowel Agent</h1>
            <p>选择一个工作区，或继续仍在运行的会话</p>
          </div>
        </header>

        <div className="cc-workspace-start__grid">
          <section>
            <h2>开始</h2>
            <button type="button" onClick={onOpenWorkspace}>
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
              </svg>
              <span>打开工作区...</span>
            </button>
            <button type="button" onClick={onNewSession}>
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="M12 5v14M5 12h14" />
              </svg>
              <span>新建 Agent 会话</span>
            </button>
            <button type="button" onClick={onOpenHistory}>
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="M4 6h16M4 12h12M4 18h9" />
              </svg>
              <span>打开历史会话</span>
            </button>
          </section>

          <section className="cc-workspace-start__recent">
            <h2>最近</h2>
            {loading && <p className="cc-workspace-start__status">正在读取...</p>}
            {error && (
              <div className="cc-workspace-start__status" role="alert">
                <span>{error}</span>
                <button type="button" onClick={onRetry}>重试</button>
              </div>
            )}
            {!loading && !error && recents.length === 0 && (
              <p className="cc-workspace-start__status">暂无最近工作区</p>
            )}
            {!loading && !error && recents.map((workspace) => (
              <button
                key={workspace.path}
                type="button"
                className="cc-workspace-start__recent-row"
                onClick={() => onSelectRecent(workspace.path)}
                disabled={!workspace.available}
              >
                <span>
                  <strong>{workspace.name}</strong>
                  <span>{workspace.path}</span>
                </span>
                <time dateTime={workspace.lastOpenedAt}>
                  {workspace.available
                    ? formatRecentTime(workspace.lastOpenedAt)
                    : "不可用"}
                </time>
              </button>
            ))}
          </section>
        </div>
      </div>
    </section>
  );
}
