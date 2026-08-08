/** 展示可搜索的研讨历史和新建入口。 */

import { useMemo, useRef, useState } from "react";
import type { Discussion } from "../domain";

interface DiscussionHistoryRailProps {
  readonly discussions: readonly Discussion[];
  readonly currentId: string | null;
  readonly loading: boolean;
  readonly onOpen: (id: string) => void;
  readonly onNew: () => void;
}

/** 渲染 216px 历史栏，不持有 discussion 业务状态。 */
export function DiscussionHistoryRail({
  discussions,
  currentId,
  loading,
  onOpen,
  onNew,
}: DiscussionHistoryRailProps) {
  const [query, setQuery] = useState("");
  const searchRef = useRef<HTMLInputElement>(null);
  const visible = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase();
    return normalized
      ? discussions.filter((item) =>
          item.topic.toLocaleLowerCase().includes(normalized),
        )
      : discussions;
  }, [discussions, query]);

  return (
    <aside className="discussion-history" aria-label="研讨历史">
      <header className="discussion-history__head">
        <strong>研讨</strong>
        <button
          type="button"
          className="discussion-history__search-button"
          onClick={() => searchRef.current?.focus()}
          aria-label="搜索研讨"
          title="搜索研讨"
        >
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <circle cx="11" cy="11" r="7" />
            <path d="m20 20-4-4" />
          </svg>
        </button>
        <button
          type="button"
          className="discussion-history__new-button"
          onClick={onNew}
          aria-label="新建研讨"
          title="新建研讨"
        >
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M12 5v14M5 12h14" />
          </svg>
        </button>
      </header>
      <label className="discussion-search">
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <circle cx="11" cy="11" r="7" />
          <path d="m20 20-4-4" />
        </svg>
        <input
          ref={searchRef}
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="搜索研讨…"
          aria-label="搜索研讨"
        />
      </label>
      <div className="discussion-history__list">
        {loading && discussions.length === 0 && <p>正在读取…</p>}
        {!loading && visible.length === 0 && <p>暂无研讨</p>}
        {visible.map((item) => (
          <button
            key={item.id}
            type="button"
            className="discussion-history__item"
            aria-current={item.id === currentId ? "page" : undefined}
            onClick={() => onOpen(item.id)}
          >
            <span className="discussion-history__title">
              <i data-status={item.status} />
              <strong>{item.topic}</strong>
            </span>
            <span>
              {historyStatus(item)}
              <b>{item.participants.length} 人</b>
            </span>
          </button>
        ))}
      </div>
      <footer>
        <i data-running={discussions.some((item) => item.status === "running")} />
        {discussions.filter((item) => item.status === "running").length} 进行中 · {discussions.length} 份记录
      </footer>
    </aside>
  );
}

function historyStatus(item: Discussion): string {
  if (item.status === "completed") return "已收尾";
  if (item.status === "stopped") return "已停止";
  if (item.status === "running") {
    return `第 ${item.active_round_number ?? 1} 轮 · 进行中`;
  }
  if (item.status === "needs_reconcile") return "等待恢复";
  if (item.status === "draft") return "尚未开始";
  return `第 ${item.active_round_number ?? 0} 轮 · 可继续`;
}
