/** 注册经过来源和参数校验的桌面 IPC，不让 renderer 直接接触系统 API。 */

import { realpath } from "node:fs/promises";
import path from "node:path";
import type { BrowserWindow, IpcMainInvokeEvent } from "electron";
import { app, dialog, ipcMain, shell } from "electron";
import { DESKTOP_IPC } from "../shared/desktop-contracts";
import type { DesktopHost } from "./host";
import {
  assertAllowedExternalUrl,
  assertPathInsideRoot,
  isTrustedRendererUrl,
} from "./ipcValidation";

export interface DesktopIpcOptions {
  readonly host: DesktopHost;
  readonly getWindow: () => BrowserWindow | null;
  readonly openTrowel: () => Promise<void>;
  readonly rendererUrl: string;
  readonly diagnosticUrl: string;
}

export function registerDesktopIpc(options: DesktopIpcOptions): () => void {
  const trusted = (event: IpcMainInvokeEvent) => {
    const senderUrl = event.senderFrame?.url ?? "";
    if (
      !isTrustedRendererUrl(
        senderUrl,
        options.rendererUrl,
        options.diagnosticUrl,
      )
    ) {
      throw new Error("untrusted desktop IPC sender");
    }
  };

  ipcMain.handle(DESKTOP_IPC.getContext, (event) => {
    trusted(event);
    return options.host.context();
  });
  ipcMain.handle(DESKTOP_IPC.getDiagnostics, (event) => {
    trusted(event);
    return options.host.diagnostics();
  });
  ipcMain.handle(DESKTOP_IPC.selectWorkdir, async (event, rawDefault) => {
    trusted(event);
    const defaultPath = optionalAbsolutePath(rawDefault);
    const window = options.getWindow();
    const dialogOptions = {
      title: "选择工作目录",
      defaultPath,
      properties: ["openDirectory", "createDirectory"] as (
        | "openDirectory"
        | "createDirectory"
      )[],
    };
    const result = window
      ? await dialog.showOpenDialog(window, dialogOptions)
      : await dialog.showOpenDialog(dialogOptions);
    return result.canceled ? null : (result.filePaths[0] ?? null);
  });
  ipcMain.handle(DESKTOP_IPC.openExternal, async (event, rawUrl) => {
    trusted(event);
    if (typeof rawUrl !== "string") throw new Error("external URL must be a string");
    await shell.openExternal(assertAllowedExternalUrl(rawUrl));
  });
  ipcMain.handle(DESKTOP_IPC.openPath, async (event, rawRequest) => {
    trusted(event);
    const request = pathRequest(rawRequest);
    const [candidate, root] = await Promise.all([
      realpath(request.path),
      realpath(request.root),
    ]);
    const allowedPath = assertPathInsideRoot(candidate, root);
    const error = await shell.openPath(allowedPath);
    if (error) throw new Error("operating system could not open the local path");
  });
  ipcMain.handle(DESKTOP_IPC.revealPath, async (event, rawRequest) => {
    trusted(event);
    const request = pathRequest(rawRequest);
    const [candidate, root] = await Promise.all([
      realpath(request.path),
      realpath(request.root),
    ]);
    shell.showItemInFolder(assertPathInsideRoot(candidate, root));
  });
  ipcMain.handle(DESKTOP_IPC.retrySidecar, async (event) => {
    trusted(event);
    await options.host.retry();
  });
  ipcMain.handle(DESKTOP_IPC.openTrowel, async (event) => {
    trusted(event);
    await options.openTrowel();
  });
  ipcMain.handle(DESKTOP_IPC.openLogs, async (event) => {
    trusted(event);
    const error = await shell.openPath(options.host.diagnostics().logDirectory);
    if (error) throw new Error("operating system could not open the log directory");
  });
  ipcMain.handle(DESKTOP_IPC.requestQuit, (event) => {
    trusted(event);
    app.quit();
  });

  return () => {
    for (const channel of Object.values(DESKTOP_IPC)) {
      ipcMain.removeHandler(channel);
    }
  };
}

function optionalAbsolutePath(value: unknown): string | undefined {
  if (value === undefined || value === null || value === "") return undefined;
  if (typeof value !== "string" || !path.isAbsolute(value) || value.length > 4096) {
    throw new Error("default directory must be an absolute path");
  }
  return value;
}

function pathRequest(value: unknown): { readonly path: string; readonly root: string } {
  if (!value || typeof value !== "object") throw new Error("invalid local path request");
  const request = value as Record<string, unknown>;
  if (typeof request.path !== "string" || typeof request.root !== "string") {
    throw new Error("invalid local path request");
  }
  if (request.path.length > 4096 || request.root.length > 4096) {
    throw new Error("local path is too long");
  }
  return { path: request.path, root: request.root };
}
