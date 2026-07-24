import {
  useEffect,
  useRef,
  useState,
  type KeyboardEvent,
} from "react";
import { createPortal } from "react-dom";

import type { AgentHistoryRow } from "../../api/agent";

interface SessionSwitcherProps {
  readonly history: readonly AgentHistoryRow[];
  readonly loading: boolean;
  readonly loadingMore: boolean;
  readonly hasMore: boolean;
  readonly error?: string | null;
  readonly onLoadMore: () => void;
  readonly onRetry?: () => void;
  readonly onPick: (row: AgentHistoryRow) => void;
  readonly onNew: () => void;
}

function formatTime(value: number | string): string {
  const epoch = typeof value === "number" ? value * 1000 : Date.parse(value);
  if (!Number.isFinite(epoch)) return "";
  const date = new Date(epoch);
  const now = new Date();
  const clock = date.toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
  if (date.toDateString() === now.toDateString()) return `今天 ${clock}`;
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (date.toDateString() === yesterday.toDateString()) return `昨天 ${clock}`;
  return date.toLocaleString("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

const RUNTIME_LABEL: Record<string, string> = {
  claude_code: "Claude",
  codex: "Codex",
};

export function SessionSwitcher({
  history,
  loading,
  loadingMore,
  hasMore,
  error = null,
  onLoadMore,
  onRetry,
  onPick,
  onNew,
}: SessionSwitcherProps) {
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    setActiveIndex((current) => Math.min(current, Math.max(history.length - 1, 0)));
  }, [history.length, open]);

  function openModal(): void {
    setActiveIndex(0);
    setOpen(true);
    requestAnimationFrame(() => listRef.current?.focus());
  }

  function closeModal(): void {
    setOpen(false);
    requestAnimationFrame(() => triggerRef.current?.focus());
  }

  function choose(row: AgentHistoryRow): void {
    if (!row.native_session_id) return;
    onPick(row);
    closeModal();
  }

  function handleListKeyDown(event: KeyboardEvent<HTMLDivElement>): void {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const direction = event.key === "ArrowDown" ? 1 : -1;
      setActiveIndex((current) => {
        const next = Math.max(0, Math.min(history.length - 1, current + direction));
        const element = document.getElementById(`history-row-${next}`);
        if (typeof element?.scrollIntoView === "function") {
          element.scrollIntoView({ block: "nearest" });
        }
        return next;
      });
      return;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      const row = history[activeIndex];
      if (row) choose(row);
    }
  }

  const status = loading
    ? "载入中…"
    : loadingMore
      ? "载入中…"
      : hasMore
        ? `已加载 ${history.length} 个会话`
        : `已加载全部 ${history.length} 个会话`;

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
      <button
        ref={triggerRef}
        type="button"
        className="cc-switcher__history"
        onClick={openModal}
        aria-haspopup="dialog"
        aria-expanded={open}
      >
        历史会话
      </button>
      {open &&
        createPortal(
          <div
            className="cc-modal-backdrop cc-history-backdrop"
            onClick={closeModal}
            role="presentation"
          >
            <section
              className="cc-modal cc-history-modal"
              role="dialog"
              aria-modal="true"
              aria-labelledby="history-modal-title"
              onClick={(event) => event.stopPropagation()}
              onKeyDown={(event) => {
                if (event.key === "Escape") {
                  event.preventDefault();
                  closeModal();
                }
              }}
            >
              <header className="cc-modal__head">
                <span className="cc-modal__title" id="history-modal-title">
                  历史会话
                </span>
                <button
                  type="button"
                  className="cc-modal__close"
                  onClick={closeModal}
                  aria-label="关闭历史会话"
                >
                  esc
                </button>
              </header>
              <div
                ref={listRef}
                className="history-list"
                role="listbox"
                aria-label="历史会话"
                tabIndex={0}
                onKeyDown={handleListKeyDown}
                onScroll={(event) => {
                  const target = event.currentTarget;
                  if (
                    hasMore &&
                    !loadingMore &&
                    target.scrollTop + target.clientHeight >= target.scrollHeight - 24
                  ) {
                    onLoadMore();
                  }
                }}
              >
                {loading && history.length === 0 && (
                  <div className="history-empty">载入中…</div>
                )}
                {!loading && error && history.length === 0 && (
                  <div className="history-empty" role="alert">
                    历史会话载入失败
                  </div>
                )}
                {!loading && !error && history.length === 0 && (
                  <div className="history-empty">暂无历史</div>
                )}
                {history.map((row, index) => (
                  <button
                    key={`${row.runtime}-${row.native_session_id ?? index}`}
                    id={`history-row-${index}`}
                    type="button"
                    className="history-row"
                    role="option"
                    aria-selected={index === activeIndex}
                    disabled={!row.native_session_id}
                    onMouseEnter={() => setActiveIndex(index)}
                    onClick={() => choose(row)}
                  >
                    <span
                      className={`history-row__badge cc-runtime-badge cc-runtime-badge--${row.runtime}`}
                    >
                      {RUNTIME_LABEL[row.runtime] ?? row.runtime}
                    </span>
                    <span className="history-row__body">
                      <span className="history-row__title">
                        {row.title || "(无标题)"}
                      </span>
                      <span className="history-row__time">
                        {formatTime(row.updated_at)}
                      </span>
                    </span>
                  </button>
                ))}
              </div>
              <footer className="cc-modal__foot">
                <div className="history-status" role="status" aria-live="polite">
                  {(loading || loadingMore) && (
                    <span className="history-spinner" aria-hidden="true" />
                  )}
                  <span>{error ? "载入失败" : status}</span>
                </div>
                {error && onRetry && (
                  <button type="button" className="cc-btn" onClick={onRetry}>
                    重试
                  </button>
                )}
                <button type="button" className="cc-btn" onClick={closeModal}>
                  取消
                </button>
              </footer>
            </section>
          </div>,
          document.body,
        )}
    </div>
  );
}
