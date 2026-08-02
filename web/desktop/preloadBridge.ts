/** 构造 preload 暴露给 renderer 的固定 IPC 方法表。 */

import {
  DESKTOP_IPC,
  type DesktopBridge,
  type DesktopContext,
  type DesktopDiagnosticState,
} from "../shared/desktop-contracts";

export type InvokeDesktopIpc = (
  channel: string,
  ...args: readonly unknown[]
) => Promise<unknown>;

export function createDesktopBridge(invoke: InvokeDesktopIpc): DesktopBridge {
  return Object.freeze({
    getContext: () =>
      invoke(DESKTOP_IPC.getContext) as Promise<DesktopContext>,
    selectWorkdir: (defaultPath?: string) =>
      invoke(DESKTOP_IPC.selectWorkdir, defaultPath) as Promise<string | null>,
    openExternal: async (url: string) => {
      await invoke(DESKTOP_IPC.openExternal, url);
    },
    openPath: async (path: string, root: string) => {
      await invoke(DESKTOP_IPC.openPath, { path, root });
    },
    requestQuit: async () => {
      await invoke(DESKTOP_IPC.requestQuit);
    },
    getDiagnostics: () =>
      invoke(DESKTOP_IPC.getDiagnostics) as Promise<DesktopDiagnosticState>,
    retrySidecar: async () => {
      await invoke(DESKTOP_IPC.retrySidecar);
    },
    openTrowel: async () => {
      await invoke(DESKTOP_IPC.openTrowel);
    },
    openLogs: async () => {
      await invoke(DESKTOP_IPC.openLogs);
    },
  });
}
