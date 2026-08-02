/** 验证 macOS 关闭窗口只隐藏界面，真正退出时才允许销毁窗口。 */

// @vitest-environment node

import { expect, it, vi } from "vitest";
import { handleDesktopWindowClose } from "./windowClosePolicy";

it("hides the macOS window while the application keeps running", () => {
  const event = { preventDefault: vi.fn() };
  const window = { hide: vi.fn() };

  handleDesktopWindowClose(event, window, {
    platform: "darwin",
    isFinalQuit: false,
  });

  expect(event.preventDefault).toHaveBeenCalledOnce();
  expect(window.hide).toHaveBeenCalledOnce();
});

it.each(["win32", "linux"])(
  "allows the %s window to close normally",
  (platform) => {
    const event = { preventDefault: vi.fn() };
    const window = { hide: vi.fn() };

    handleDesktopWindowClose(event, window, {
      platform,
      isFinalQuit: false,
    });

    expect(event.preventDefault).not.toHaveBeenCalled();
    expect(window.hide).not.toHaveBeenCalled();
  },
);

it("allows macOS windows to close after bounded shutdown finishes", () => {
  const event = { preventDefault: vi.fn() };
  const window = { hide: vi.fn() };

  handleDesktopWindowClose(event, window, {
    platform: "darwin",
    isFinalQuit: true,
  });

  expect(event.preventDefault).not.toHaveBeenCalled();
  expect(window.hide).not.toHaveBeenCalled();
});
