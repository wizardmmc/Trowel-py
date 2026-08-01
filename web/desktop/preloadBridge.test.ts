/** 验证 preload 只把固定的桌面命令映射到 IPC。 */

// @vitest-environment node

import { expect, it, vi } from "vitest";
import { createDesktopBridge } from "./preloadBridge";

it("exposes only typed desktop operations", async () => {
  const invoke = vi.fn().mockResolvedValue({ ok: true });
  const bridge = createDesktopBridge(invoke);

  expect(Object.keys(bridge).sort()).toEqual([
    "getContext",
    "getDiagnostics",
    "openExternal",
    "openLogs",
    "openPath",
    "requestQuit",
    "retrySidecar",
    "selectWorkdir",
  ]);

  await bridge.openExternal("https://example.com/docs");
  await bridge.openPath("/repo/readme.md", "/repo");
  await bridge.selectWorkdir("/repo");

  expect(invoke).toHaveBeenNthCalledWith(
    1,
    "desktop:open-external",
    "https://example.com/docs",
  );
  expect(invoke).toHaveBeenNthCalledWith(2, "desktop:open-path", {
    path: "/repo/readme.md",
    root: "/repo",
  });
  expect(invoke).toHaveBeenNthCalledWith(3, "desktop:select-workdir", "/repo");
});
