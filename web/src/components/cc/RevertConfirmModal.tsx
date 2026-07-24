import { useEffect, useRef } from "react";

import type { Turn } from "../../stores/ccStore";

interface RevertConfirmModalProps {
  readonly lostTurns: readonly Turn[];
  readonly onConfirm: () => void;
  readonly onCancel: () => void;
}

export function RevertConfirmModal({
  lostTurns,
  onConfirm,
  onCancel,
}: RevertConfirmModalProps) {
  const cancelRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    cancelRef.current?.focus();
  }, []);
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onCancel();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel]);

  return (
    <div className="cc-revert-scrim" role="dialog" aria-modal="true">
      <div className="cc-revert-modal">
        <h3 className="cc-revert-modal__title">
          <span className="cc-revert-modal__icon" aria-hidden>⚠</span>
          回滚到这轮之前？
        </h3>
        <p className="cc-revert-modal__lead">
          这将<b>永久丢弃</b>以下 {lostTurns.length} 轮对话：
        </p>
        <ul className="cc-revert-modal__lose">
          {lostTurns.map((t) => (
            <li key={t.id}>
              <code>{truncate(t.userText || "(无文本)", 48)}</code>
            </li>
          ))}
        </ul>
        <p className="cc-revert-modal__note">
          同时 <b>git restore</b> 这些轮对工作区文件的改动，回到这轮开始前的状态。
          CC 会以 <code>--resume</code> 从更短的历史接着聊。
        </p>
        <p className="cc-revert-modal__irreversible">此操作不可撤销。</p>
        <div className="cc-revert-modal__actions">
          <button
            ref={cancelRef}
            type="button"
            className="cc-revert-modal__cancel"
            onClick={onCancel}
          >
            取消
          </button>
          <button type="button" className="cc-revert-modal__confirm" onClick={onConfirm}>
            确认回滚
          </button>
        </div>
      </div>
    </div>
  );
}

function truncate(s: string, n: number): string {
  return s.length > n ? s.slice(0, n) + "…" : s;
}
