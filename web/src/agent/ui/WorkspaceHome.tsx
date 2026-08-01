/** 展示已选工作区的新建入口和该目录的历史会话。 */

import type { AgentHistoryRow } from "../application";
import { getExpectedRuntimePresentation } from "../runtimes";

interface WorkspaceHomeProps {
  readonly workdir: string;
  readonly history: readonly AgentHistoryRow[];
  readonly loadingHistory: boolean;
  readonly loadingMoreHistory: boolean;
  readonly historyHasMore: boolean;
  readonly historyError: string | null;
  readonly onNewSession: () => void;
  readonly onSwitchWorkspace: () => void;
  readonly onPickHistory: (row: AgentHistoryRow) => void;
  readonly onLoadMoreHistory: () => void;
  readonly onRetryHistory: () => void;
}

/** 返回跨 Unix 与 Windows 路径都可用的目录名称。 */
function workdirName(workdir: string): string {
  const normalized = workdir.replace(/[\\/]+$/, "");
  return normalized.split(/[\\/]/).pop() || workdir;
}

/** 渲染选中工作区但尚未选择 live session 时的中间页。 */
export function WorkspaceHome({
  workdir,
  history,
  loadingHistory,
  loadingMoreHistory,
  historyHasMore,
  historyError,
  onNewSession,
  onSwitchWorkspace,
  onPickHistory,
  onLoadMoreHistory,
  onRetryHistory,
}: WorkspaceHomeProps) {
  return (
    <section className="cc-workspace-home" aria-label="当前工作区">
      <div className="cc-workspace-home__inner">
        <div className="cc-workspace-home__kicker">
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
          </svg>
          工作区
        </div>
        <h1>{workdirName(workdir)}</h1>
        <p className="cc-workspace-home__path">{workdir}</p>
        <div className="cc-workspace-home__actions">
          <button
            type="button"
            className="cc-workspace-home__primary"
            onClick={onNewSession}
          >
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M12 5v14M5 12h14" />
            </svg>
            新建会话
          </button>
          <button type="button" onClick={onSwitchWorkspace}>
            切换工作区
          </button>
        </div>

        <section className="cc-workspace-home__history" aria-label="历史会话">
          <h2>历史会话</h2>
          {loadingHistory && <p>正在读取...</p>}
          {historyError && (
            <div role="alert">
              <span>{historyError}</span>
              <button type="button" onClick={onRetryHistory}>重试</button>
            </div>
          )}
          {!loadingHistory && !historyError && history.length === 0 && (
            <p>这个工作区还没有历史会话</p>
          )}
          {history.map((row) => (
            <button
              key={`${row.runtime}:${row.native_session_id ?? row.title}:${row.updated_at}`}
              type="button"
              className="cc-workspace-home__history-row"
              disabled={!row.native_session_id}
              onClick={() => onPickHistory(row)}
            >
              <span className="cc-workspace-home__history-dot" aria-hidden="true" />
              <span>{row.title}</span>
              <span>{getExpectedRuntimePresentation(row.runtime).shortLabel}</span>
            </button>
          ))}
          {historyHasMore && !historyError && (
            <button
              type="button"
              className="cc-workspace-home__load-more"
              disabled={loadingMoreHistory}
              onClick={onLoadMoreHistory}
            >
              {loadingMoreHistory ? "正在加载..." : "加载更多"}
            </button>
          )}
        </section>
      </div>
    </section>
  );
}
