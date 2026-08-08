/** 创建安全的 Trowel 窗口，并限制导航、弹窗和权限请求。 */

import path from "node:path";
import { BrowserWindow, shell } from "electron";
import { DESKTOP_LAYOUT_PX } from "../shared/desktop-layout";
import {
  assertAllowedExternalUrl,
  isTrustedRendererLocation,
} from "./ipcValidation";
export { focusDesktopWindow } from "./windowFocus";

export interface DesktopWindowOptions {
  readonly preloadPath: string;
  readonly trustedRendererUrl: string;
}

export function createDesktopWindow(options: DesktopWindowOptions): BrowserWindow {
  const window = new BrowserWindow({
    width: 1360,
    height: 900,
    minWidth: 900,
    minHeight: 640,
    show: false,
    backgroundColor: "#fffdf7",
    title: "Trowel",
    ...(process.platform === "darwin"
      ? {
          titleBarStyle: "hidden" as const,
          trafficLightPosition: {
            x: DESKTOP_LAYOUT_PX.trafficLightX,
            y: DESKTOP_LAYOUT_PX.trafficLightY,
          },
        }
      : {}),
    webPreferences: {
      preload: path.resolve(options.preloadPath),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
    },
  });

  window.once("ready-to-show", () => window.show());
  window.webContents.on("will-attach-webview", (event) => event.preventDefault());
  window.webContents.on("will-navigate", (event, url) => {
    if (isAllowedNavigation(url, options.trustedRendererUrl)) return;
    event.preventDefault();
    void openExternalIfAllowed(url);
  });
  window.webContents.setWindowOpenHandler(({ url }) => {
    void openExternalIfAllowed(url);
    return { action: "deny" };
  });
  window.webContents.session.setPermissionRequestHandler(
    (_webContents, _permission, callback) => callback(false),
  );
  return window;
}

function isAllowedNavigation(candidate: string, trustedRendererUrl: string): boolean {
  return isTrustedRendererLocation(candidate, trustedRendererUrl);
}

async function openExternalIfAllowed(rawUrl: string): Promise<void> {
  try {
    const url = new URL(assertAllowedExternalUrl(rawUrl));
    if (["127.0.0.1", "localhost"].includes(url.hostname)) return;
    await shell.openExternal(url.toString());
  } catch {
    // 被拒绝的导航不会转交系统，也不向 renderer 泄露主进程错误。
  }
}
