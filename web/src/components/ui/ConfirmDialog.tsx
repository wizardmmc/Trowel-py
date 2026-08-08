/** 提供替代浏览器原生 confirm 的 Trowel 统一确认弹窗。 */

import { useEffect } from "react";
import { createPortal } from "react-dom";
import "./confirm-dialog.css";

export interface ConfirmDialogProps {
  readonly title: string;
  readonly description: string;
  readonly confirmLabel: string;
  readonly onConfirm: () => void;
  readonly onCancel: () => void;
  readonly tone?: "default" | "danger";
}

/** 在 body 顶层渲染可键盘关闭的确认弹窗，避免平台相关的浏览器外观。 */
export function ConfirmDialog({
  title,
  description,
  confirmLabel,
  onConfirm,
  onCancel,
  tone = "default",
}: ConfirmDialogProps) {
  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onCancel();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onCancel]);

  return createPortal(
    <div className="confirm-dialog__backdrop" onMouseDown={onCancel}>
      <section
        className="confirm-dialog"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="confirm-dialog-title"
        aria-describedby="confirm-dialog-description"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="confirm-dialog__body">
          <span className={`confirm-dialog__mark is-${tone}`} aria-hidden="true">!</span>
          <div>
            <h2 id="confirm-dialog-title">{title}</h2>
            <p id="confirm-dialog-description">{description}</p>
          </div>
        </div>
        <footer>
          <button type="button" onClick={onCancel} autoFocus>取消</button>
          <button
            type="button"
            className={tone === "danger" ? "is-danger" : "is-primary"}
            onClick={onConfirm}
          >
            {confirmLabel}
          </button>
        </footer>
      </section>
    </div>,
    document.body,
  );
}
