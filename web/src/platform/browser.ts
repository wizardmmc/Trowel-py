/** 为普通浏览器保留现有 Web 能力和目录选择降级语义。 */

import type { PlatformPort } from "./contracts";

export const browserPlatform: PlatformPort = {
  environment: "browser",
  appVersion: "development",
  selectWorkdir: async () => null,
  openExternal: async (url) => {
    window.open(url, "_blank", "noopener,noreferrer");
  },
  openPath: async () => {
    throw new Error("browser mode cannot open a raw local path");
  },
  revealPath: async () => {
    throw new Error("browser mode cannot reveal a raw local path");
  },
  requestQuit: async () => undefined,
  getDiagnostics: async () => null,
};
