/** 验证第二次启动会恢复并聚焦已有桌面窗口。 */

// @vitest-environment node

import { expect, it, vi } from "vitest";
import { focusDesktopWindow } from "./windowFocus";

it("restores a minimized primary window before focusing it", () => {
  const window = {
    isDestroyed: vi.fn().mockReturnValue(false),
    isMinimized: vi.fn().mockReturnValue(true),
    restore: vi.fn(),
    show: vi.fn(),
    focus: vi.fn(),
  };

  focusDesktopWindow(window);

  expect(window.restore).toHaveBeenCalledOnce();
  expect(window.show).toHaveBeenCalledOnce();
  expect(window.focus).toHaveBeenCalledOnce();
});
