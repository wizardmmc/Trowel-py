import type { CcSessionSummary } from "../../api/cc";

/**
 * History session dropdown — pick a past CC session to resume, or start fresh.
 * Listing comes from GET /sessions?workdir=... (any CC session under that
 * workdir, including months-old ones). Selecting one calls onPick with its
 * cc_session_id; the parent opens a resume session and loads history.
 */
interface SessionSwitcherProps {
  readonly history: readonly CcSessionSummary[];
  readonly loading: boolean;
  readonly onPick: (ccSessionId: string) => void;
  readonly onNew: () => void;
}

function formatTime(epoch: number): string {
  return new Date(epoch * 1000).toLocaleString();
}

export function SessionSwitcher({
  history,
  loading,
  onPick,
  onNew,
}: SessionSwitcherProps) {
  return (
    <div className="cc-switcher">
      <button
        type="button"
        className="cc-switcher__new"
        onClick={onNew}
        title="新会话"
      >
        + 新会话
      </button>
      <details className="cc-switcher__dropdown">
        <summary className="cc-switcher__summary">历史会话（{history.length}）</summary>
        <div className="cc-switcher__list">
          {loading && <div className="cc-switcher__loading">载入中…</div>}
          {!loading && history.length === 0 && (
            <div className="cc-switcher__empty">暂无历史</div>
          )}
          {!loading &&
            history.map((s) => (
              <button
                key={s.cc_session_id}
                type="button"
                className="cc-switcher__item"
                onClick={() => onPick(s.cc_session_id)}
                title={formatTime(s.updated_at)}
              >
                <span className="cc-switcher__item-title">{s.title || "(无标题)"}</span>
                <span className="cc-switcher__item-time">
                  {formatTime(s.updated_at)}
                </span>
              </button>
            ))}
        </div>
      </details>
    </div>
  );
}
