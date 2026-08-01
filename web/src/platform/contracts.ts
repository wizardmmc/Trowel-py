/** 定义产品 UI 可以使用的 browser 与 desktop 平台能力。 */

import type { DesktopDiagnosticState } from "../../shared/desktop-contracts";

export interface PlatformPort {
  readonly environment: "browser" | "desktop";
  readonly appVersion: string;
  readonly selectWorkdir: (defaultPath?: string) => Promise<string | null>;
  readonly openExternal: (url: string) => Promise<void>;
  readonly openPath: (path: string, root: string) => Promise<void>;
  readonly requestQuit: () => Promise<void>;
  readonly getDiagnostics: () => Promise<DesktopDiagnosticState | null>;
}
