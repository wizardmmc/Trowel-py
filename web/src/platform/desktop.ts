/** 把 preload 的白名单桥接包装成 renderer 使用的平台实现。 */

import type { DesktopBridge, DesktopContext } from "../../shared/desktop-contracts";
import type { PlatformPort } from "./contracts";

export function createDesktopPlatform(
  bridge: DesktopBridge,
  context: DesktopContext,
): PlatformPort {
  return {
    environment: "desktop",
    appVersion: context.appVersion,
    selectWorkdir: (defaultPath) => bridge.selectWorkdir(defaultPath),
    openExternal: (url) => bridge.openExternal(url),
    openPath: (path, root) => bridge.openPath(path, root),
    revealPath: (path, root) => bridge.revealPath(path, root),
    requestQuit: () => bridge.requestQuit(),
    getDiagnostics: () => bridge.getDiagnostics(),
  };
}
