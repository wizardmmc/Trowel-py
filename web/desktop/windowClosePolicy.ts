/** 定义关闭桌面窗口时隐藏界面或允许销毁的单一策略。 */

export interface DesktopWindowCloseEvent {
  readonly preventDefault: () => void;
}

export interface HideableDesktopWindow {
  readonly hide: () => void;
}

export interface DesktopWindowCloseOptions {
  readonly platform: string;
  readonly isFinalQuit: boolean;
}

/** macOS 正常关窗只隐藏界面；有界退出完成后才允许 Electron 销毁窗口。 */
export function handleDesktopWindowClose(
  event: DesktopWindowCloseEvent,
  window: HideableDesktopWindow,
  options: DesktopWindowCloseOptions,
): void {
  if (options.platform !== "darwin" || options.isFinalQuit) return;
  event.preventDefault();
  window.hide();
}
