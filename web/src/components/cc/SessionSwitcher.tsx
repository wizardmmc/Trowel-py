import type { AgentHistoryRow } from "../../api/agent";

/**
 * slice-072: history session dropdown — pick a past session to resume. Each
 * row carries its runtime (CC jsonl or Codex thread); onPick receives the
 * whole row so the store resumes through the originating runtime only (spec
 * C-2: cross-runtime resume is forbidden). A row whose native_session_id is
 * null (a fresh Codex binding with no thread yet) is rendered but disabled —
 * there is nothing to resume.
 */
interface SessionSwitcherProps {
  readonly history: readonly AgentHistoryRow[];
  /** total sessions on disk; history.length is the capped list shown. */
  readonly total: number;
  readonly loading: boolean;
  readonly onPick: (row: AgentHistoryRow) => void;
  readonly onNew: () => void;
}

function formatTime(value: number | string): string {
  const epoch = typeof value === "number" ? value * 1000 : Date.parse(value);
  if (!Number.isFinite(epoch)) return "";
  return new Date(epoch).toLocaleString();
}

const RUNTIME_LABEL: Record<string, string> = {
  claude_code: "CC",
  codex: "Codex",
};

export function SessionSwitcher({
  history,
  total,
  loading,
  onPick,
  onNew,
}: SessionSwitcherProps) {
  // When the list is capped (more on disk than shown), surface both numbers so
  // the count isn't misleading; otherwise just show the total.
  const capped = total > history.length;
  const countLabel = capped ? `共 ${total} · 最近 ${history.length}` : `共 ${total}`;
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
        <summary className="cc-switcher__summary">历史会话（{countLabel}）</summary>
        <div className="cc-switcher__list">
          {loading && <div className="cc-switcher__loading">载入中…</div>}
          {!loading && history.length === 0 && (
            <div className="cc-switcher__empty">暂无历史</div>
          )}
          {!loading &&
            history.map((row, idx) => {
              const disabled = row.native_session_id == null;
              return (
                <button
                  key={`${row.runtime}-${row.native_session_id ?? idx}`}
                  type="button"
                  className="cc-switcher__item"
                  disabled={disabled}
                  onClick={() => onPick(row)}
                  title={
                    disabled ? "无 native id，无法恢复" : formatTime(row.updated_at)
                  }
                >
                  <span className="cc-switcher__item-title">
                    <span
                      className={`cc-runtime-badge cc-runtime-badge--${row.runtime}`}
                    >
                      {RUNTIME_LABEL[row.runtime] ?? row.runtime}
                    </span>
                    {row.title || "(无标题)"}
                  </span>
                  <span className="cc-switcher__item-time">
                    {formatTime(row.updated_at)}
                  </span>
                </button>
              );
            })}
        </div>
      </details>
    </div>
  );
}
