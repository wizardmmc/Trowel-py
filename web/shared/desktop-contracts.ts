/** 定义 Electron main、preload 与 renderer 共同使用的窄桌面契约。 */

export const DESKTOP_PROTOCOL_VERSION = 1;

export const DESKTOP_IPC = {
  getContext: "desktop:get-context",
  selectWorkdir: "desktop:select-workdir",
  openExternal: "desktop:open-external",
  openPath: "desktop:open-path",
  requestQuit: "desktop:request-quit",
  getDiagnostics: "desktop:get-diagnostics",
  retrySidecar: "desktop:retry-sidecar",
  openTrowel: "desktop:open-trowel",
  openLogs: "desktop:open-logs",
} as const;

export type SidecarErrorCategory =
  | "executable_missing"
  | "port_or_permission"
  | "version_mismatch"
  | "readiness_timeout"
  | "early_exit";

export interface DesktopTransportConfig {
  readonly baseUrl: string;
  readonly credential: string;
}

export interface DesktopContext {
  readonly environment: "desktop";
  readonly appVersion: string;
  readonly instanceId: string;
  readonly transport: DesktopTransportConfig;
}

export interface DesktopDiagnosticState {
  readonly status: "starting" | "ready" | "failed";
  readonly category: SidecarErrorCategory | null;
  readonly message: string | null;
  readonly exitCode: number | null;
  readonly logDirectory: string;
}

export interface DesktopBridge {
  readonly getContext: () => Promise<DesktopContext>;
  readonly selectWorkdir: (defaultPath?: string) => Promise<string | null>;
  readonly openExternal: (url: string) => Promise<void>;
  readonly openPath: (path: string, root: string) => Promise<void>;
  readonly requestQuit: () => Promise<void>;
  readonly getDiagnostics: () => Promise<DesktopDiagnosticState>;
  readonly retrySidecar: () => Promise<void>;
  readonly openTrowel: () => Promise<void>;
  readonly openLogs: () => Promise<void>;
}
