/** 提供不依赖 Electron 运行时的单实例窗口恢复与聚焦策略。 */

export interface FocusableDesktopWindow {
  readonly isDestroyed: () => boolean;
  readonly isMinimized: () => boolean;
  readonly restore: () => void;
  readonly show: () => void;
  readonly focus: () => void;
}

export function focusDesktopWindow(window: FocusableDesktopWindow | null): void {
  if (!window || window.isDestroyed()) return;
  if (window.isMinimized()) window.restore();
  window.show();
  window.focus();
}
