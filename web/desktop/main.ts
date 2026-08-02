/** 组装单实例 Electron Host、Python sidecar、窗口和桌面 IPC 生命周期。 */

import path from "node:path";
import { pathToFileURL } from "node:url";
import { randomBytes, randomUUID } from "node:crypto";
import {
  app,
  Menu,
  nativeImage,
  session,
  shell,
  Tray,
  type BrowserWindow,
} from "electron";
import {
  applicationMenuTemplate,
  statusMenuTemplate,
  type DesktopMenuActions,
} from "./desktopMenus";
import {
  resolveDesktopDataMode,
  resolveDesktopPaths,
} from "./desktopDataPaths";
import { DesktopHost } from "./host";
import { armHostExitWatchdog } from "./hostExitWatchdog";
import { registerDesktopIpc } from "./ipc";
import { createLifecycleLogger } from "./lifecycleLogger";
import {
  decideRendererRecovery,
  type DesktopPage,
} from "./rendererRecovery";
import { configureRendererSession } from "./rendererSession";
import {
  removeAgentServiceDescriptor,
  writeAgentServiceDescriptor,
} from "./serviceDescriptor";
import { createDesktopWindow, focusDesktopWindow } from "./window";
import { handleDesktopWindowClose } from "./windowClosePolicy";
import { configureSafeStorageForSmoke } from "./safeStoragePolicy";
import type { SidecarLaunchCommand } from "./sidecar";

const PRODUCT_NAME = "Trowel";

const projectRoot = path.resolve(
  process.env.TROWEL_PROJECT_ROOT ?? path.join(__dirname, "../../.."),
);
const rendererEntry = path.resolve(__dirname, "../../dist/index.html");
const diagnosticEntry = path.resolve(__dirname, "diagnostic.html");
const rendererUrl =
  process.env.TROWEL_RENDERER_URL ?? pathToFileURL(rendererEntry).toString();
const diagnosticUrl = pathToFileURL(diagnosticEntry).toString();
const preloadPath = path.resolve(__dirname, "preload.js");
const rendererSmoke = process.env.TROWEL_DESKTOP_SMOKE === "1";
const diagnosticSmoke = process.env.TROWEL_DESKTOP_DIAGNOSTIC_SMOKE === "1";
const residencySmoke = process.env.TROWEL_DESKTOP_RESIDENCY_SMOKE === "1";
const singleInstanceSmoke =
  process.env.TROWEL_DESKTOP_SINGLE_INSTANCE_SMOKE === "1";
const rendererCrashSmoke =
  process.env.TROWEL_DESKTOP_RENDERER_CRASH_SMOKE === "1";
const serviceDescriptorPath = process.env.TROWEL_DESKTOP_SERVICE_FILE;

configureSafeStorageForSmoke(
  app.commandLine,
  rendererSmoke || diagnosticSmoke || residencySmoke || singleInstanceSmoke || rendererCrashSmoke,
);
app.setName(PRODUCT_NAME);

const desktopDataMode = resolveDesktopDataMode(
  process.env.TROWEL_DESKTOP_DATA_MODE,
  app.isPackaged,
);
const initialDesktopPaths = resolveDesktopPaths({
  appDataDirectory: app.getPath("appData"),
  logsDirectory: app.getPath("logs"),
  mode: desktopDataMode,
  dataDirectoryOverride: process.env.TROWEL_DESKTOP_DATA_DIR,
  logDirectoryOverride: process.env.TROWEL_DESKTOP_LOG_DIR,
  electronUserDataDirectoryOverride: process.env.TROWEL_ELECTRON_USER_DATA_DIR,
});
if (initialDesktopPaths.electronUserDataDirectory) {
  app.setPath("userData", initialDesktopPaths.electronUserDataDirectory);
}

const hasSingleInstanceLock = app.requestSingleInstanceLock();
if (!hasSingleInstanceLock) {
  app.quit();
} else {
  void startDesktopApplication().catch(() => {
    console.error("Trowel desktop host failed before diagnostics were available.");
    app.exit(1);
  });
}

async function startDesktopApplication(): Promise<void> {
  const instanceId = randomUUID();
  const credential = randomBytes(32).toString("base64url");
  const desktopPaths = resolveDesktopPaths({
    appDataDirectory: app.getPath("appData"),
    logsDirectory: app.getPath("logs"),
    mode: desktopDataMode,
    dataDirectoryOverride: process.env.TROWEL_DESKTOP_DATA_DIR,
    logDirectoryOverride: process.env.TROWEL_DESKTOP_LOG_DIR,
    electronUserDataDirectoryOverride: process.env.TROWEL_ELECTRON_USER_DATA_DIR,
  });
  const { dataDirectory, logDirectory } = desktopPaths;
  const logLifecycle = createLifecycleLogger(logDirectory);

  await app.whenReady();
  await configureRendererSession(session.defaultSession);
  let mainWindow: BrowserWindow | null = null;
  let removeIpcHandlers: (() => void) | null = null;
  let host: DesktopHost | null = null;
  let currentPage: DesktopPage = null;
  let previousRendererCrashAt: number | null = null;
  let rendererCrashSmokeStarted = false;
  let finalQuit = false;
  let quitPromise: Promise<void> | null = null;
  let statusTray: Tray | null = null;

  /** readiness 通过后向同一开发链中的浏览器发布共享服务。 */
  const publishAgentService = async (): Promise<void> => {
    if (!serviceDescriptorPath) return;
    if (!host) throw new Error("desktop host is not initialized");
    const context = host.context();
    await writeAgentServiceDescriptor(serviceDescriptorPath, {
      serviceInstanceId: context.instanceId,
      baseUrl: context.transport.baseUrl,
      credential: context.transport.credential,
    });
  };

  /** 仅撤销当前 Host 自己发布的共享服务。 */
  const removeAgentService = async (): Promise<void> => {
    if (!serviceDescriptorPath) return;
    await removeAgentServiceDescriptor(serviceDescriptorPath, instanceId);
  };

  const ensureWindow = () => {
    if (mainWindow && !mainWindow.isDestroyed()) return mainWindow;
    mainWindow = createDesktopWindow({ preloadPath, trustedRendererUrl: rendererUrl });
    mainWindow.webContents.on("render-process-gone", (_event, details) => {
      const crashedPage = currentPage;
      currentPage = null;
      const now = Date.now();
      const action = decideRendererRecovery({
        crashedPage,
        finalQuit,
        reason: details.reason,
        previousCrashAt: previousRendererCrashAt,
        now,
      });
      if (crashedPage === "renderer" && details.reason !== "clean-exit") {
        previousRendererCrashAt = now;
        logLifecycle("renderer_gone", details.reason);
      }
      if (action === "reload") {
        void loadRenderer().catch((error) => {
          logLifecycle(
            "renderer_recovery_failed",
            error instanceof Error ? error.name : "unknown",
          );
        });
      } else if (action === "diagnostics") {
        void loadDiagnostics().catch((error) => {
          logLifecycle(
            "diagnostics_open_failed",
            error instanceof Error ? error.name : "unknown",
          );
        });
      }
    });
    mainWindow.on("close", (event) => {
      if (!mainWindow) return;
      handleDesktopWindowClose(event, mainWindow, {
        platform: process.platform,
        isFinalQuit: finalQuit,
      });
      if (process.platform === "darwin" && !finalQuit) {
        logLifecycle("window_hidden");
      }
    });
    mainWindow.on("closed", () => {
      mainWindow = null;
      currentPage = null;
    });
    return mainWindow;
  };
  const loadRenderer = async () => {
    await publishAgentService();
    const window = ensureWindow();
    if (process.env.TROWEL_RENDERER_URL) {
      await window.loadURL(rendererUrl);
    } else {
      await window.loadFile(rendererEntry);
    }
    currentPage = "renderer";
    logLifecycle("renderer_loaded");
    const runRendererCrashSmoke =
      rendererCrashSmoke && !rendererCrashSmokeStarted;
    if (rendererSmoke || residencySmoke || singleInstanceSmoke || rendererCrashSmoke) {
      try {
        await waitForRendererReady(window);
      } catch (error) {
        console.error("TROWEL_DESKTOP_SMOKE_FAILED", error);
        throw error;
      }
      if (rendererSmoke) {
        console.log("TROWEL_DESKTOP_SMOKE_OK");
        app.quit();
      }
      if (residencySmoke) {
        try {
          if (!host) throw new Error("desktop host is not initialized");
          await verifyWindowResidency(window, host);
          console.log("TROWEL_DESKTOP_RESIDENCY_SMOKE_OK");
        } catch (error) {
          process.exitCode = 1;
          console.error("TROWEL_DESKTOP_RESIDENCY_SMOKE_FAILED", error);
        }
        app.quit();
      }
      if (runRendererCrashSmoke) {
        rendererCrashSmokeStarted = true;
        try {
          if (!host) throw new Error("desktop host is not initialized");
          await crashRendererAndVerifyRecovery(window, host);
          console.log("TROWEL_DESKTOP_RENDERER_CRASHED_SIDECAR_ALIVE");
        } catch (error) {
          process.exitCode = 1;
          console.error("TROWEL_DESKTOP_RENDERER_CRASH_SMOKE_FAILED", error);
        }
        app.quit();
      }
    }
  };
  const loadDiagnostics = async () => {
    await removeAgentService();
    const window = ensureWindow();
    await window.loadFile(diagnosticEntry);
    currentPage = "diagnostics";
    logLifecycle("diagnostics_loaded");
    if (diagnosticSmoke) {
      const state = await window.webContents.executeJavaScript(
        `({
          bridgeType: typeof window.trowelDesktop,
          title: document.querySelector('h1')?.textContent ?? '',
          buttons: document.querySelectorAll('button').length
        })`,
        true,
      );
      if (
        state?.bridgeType === "object" &&
        state?.title === "Trowel 诊断" &&
        state?.buttons === 3
      ) {
        console.log("TROWEL_DESKTOP_DIAGNOSTIC_SMOKE_OK");
        app.quit();
        return;
      }
      process.exitCode = 1;
      console.error("TROWEL_DESKTOP_DIAGNOSTIC_SMOKE_FAILED", state);
      app.quit();
    }
  };

  host = new DesktopHost(
    {
      command: resolveSidecarLaunchCommand(projectRoot),
      cwd: projectRoot,
      dataDirectory,
      logDirectory,
      dataMode: desktopDataMode,
      instanceId,
      credential,
      expectedAppVersion: app.getVersion(),
      rendererOrigin: rendererOrigin(rendererUrl),
    },
    { loadRenderer, loadDiagnostics },
  );
  removeIpcHandlers = registerDesktopIpc({
    host,
    getWindow: () => mainWindow,
    openTrowel: async () => {
      if (currentPage === "renderer") {
        focusDesktopWindow(mainWindow);
        return;
      }
      await loadRenderer();
      focusDesktopWindow(mainWindow);
    },
    rendererUrl,
    diagnosticUrl,
  });

  const menuActions: DesktopMenuActions = {
    openTrowel: () => {
      if (currentPage === "renderer") {
        focusDesktopWindow(mainWindow);
        return;
      }
      void loadRenderer().catch((error) => {
        logLifecycle(
          "renderer_open_failed",
          error instanceof Error ? error.name : "unknown",
        );
      });
    },
    openDiagnostics: () => {
      if (currentPage === "diagnostics") {
        focusDesktopWindow(mainWindow);
        return;
      }
      void loadDiagnostics().catch((error) => {
        logLifecycle(
          "diagnostics_open_failed",
          error instanceof Error ? error.name : "unknown",
        );
      });
    },
    openLogs: () => {
      void shell.openPath(logDirectory).then((message) => {
        if (message) logLifecycle("logs_open_failed", message);
      });
    },
    quitTrowel: () => app.quit(),
  };
  Menu.setApplicationMenu(
    Menu.buildFromTemplate(
      applicationMenuTemplate(PRODUCT_NAME, menuActions) as Electron.MenuItemConstructorOptions[],
    ),
  );
  if (process.platform === "darwin") {
    const trayIcon = nativeImage.createFromPath(
      path.resolve(__dirname, "assets/status-icon.png"),
    );
    statusTray = new Tray(trayIcon);
    statusTray.setToolTip(PRODUCT_NAME);
    statusTray.setContextMenu(
      Menu.buildFromTemplate(
        statusMenuTemplate(menuActions) as Electron.MenuItemConstructorOptions[],
      ),
    );
    statusTray.on("click", menuActions.openTrowel);
  }

  app.on("second-instance", () => {
    focusDesktopWindow(mainWindow);
    logLifecycle("second_instance");
    if (singleInstanceSmoke) {
      console.log("TROWEL_DESKTOP_SINGLE_INSTANCE_OK");
      app.quit();
    }
  });
  app.on("activate", () => {
    if (mainWindow && currentPage === "renderer") {
      focusDesktopWindow(mainWindow);
      return;
    }
    if (host.diagnostics().status === "ready") void loadRenderer();
    else void loadDiagnostics();
  });
  app.on("window-all-closed", () => {
    if (process.platform !== "darwin") app.quit();
  });
  app.on("before-quit", (event) => {
    if (finalQuit) return;
    event.preventDefault();
    if (quitPromise) return;
    logLifecycle("host_quit");
    quitPromise = (async () => {
      try {
        const result = await host?.stop();
        logLifecycle("host_drain_finished", result?.status ?? "closed");
      } catch (error) {
        logLifecycle(
          "host_drain_failed",
          error instanceof Error ? error.name : "unknown",
        );
      }
      try {
        await removeAgentService();
      } catch (error) {
        logLifecycle(
          "service_descriptor_remove_failed",
          error instanceof Error ? error.name : "unknown",
        );
      }
      removeIpcHandlers?.();
      removeIpcHandlers = null;
      statusTray?.destroy();
      statusTray = null;
      finalQuit = true;
      const exitCode = typeof process.exitCode === "number" ? process.exitCode : 0;
      try {
        if (await armHostExitWatchdog()) {
          logLifecycle("host_exit_watchdog_armed");
        }
      } catch (error) {
        logLifecycle(
          "host_exit_watchdog_failed",
          error instanceof Error ? error.name : "unknown",
        );
      }
      app.exit(exitCode);
      process.exit(exitCode);
    })();
  });

  logLifecycle("sidecar_starting");
  await host.start();
  const state = host.diagnostics();
  logLifecycle(
    state.status === "ready" ? "sidecar_ready" : "sidecar_failed",
    state.category,
  );
  if ((rendererSmoke || residencySmoke) && state.status !== "ready") {
    process.exitCode = 1;
    app.quit();
  }
}

/** 选择开发环境的 Python 模块入口或安装包内的冻结 sidecar。 */
function resolveSidecarLaunchCommand(root: string): SidecarLaunchCommand {
  if (process.env.TROWEL_PYTHON_EXECUTABLE) {
    return {
      executable: path.resolve(process.env.TROWEL_PYTHON_EXECUTABLE),
      args: ["-m", "trowel_py.desktop.sidecar"],
    };
  }
  if (app.isPackaged) {
    return {
      executable: path.join(process.resourcesPath, "sidecar", "trowel-sidecar"),
      args: [],
    };
  }
  return {
    executable: path.join(root, ".venv", "bin", "python"),
    args: ["-m", "trowel_py.desktop.sidecar"],
  };
}

function rendererOrigin(url: string): string {
  const parsed = new URL(url);
  return parsed.protocol === "file:" ? "null" : parsed.origin;
}

async function waitForRendererReady(window: BrowserWindow): Promise<void> {
  const deadline = Date.now() + 10_000;
  let lastState: unknown = null;
  let quietSamples = 0;
  while (Date.now() < deadline) {
    try {
      lastState = await window.webContents.executeJavaScript(
        `({
          bridgeType: typeof window.trowelDesktop,
          ready: window.__TROWEL_RENDERER_READY__ === true,
          pendingRequests: window.__TROWEL_PENDING_TRANSPORT_REQUESTS__ ?? 0,
          rootText: document.querySelector('#root')?.textContent?.slice(0, 120) ?? ''
        })`,
        true,
      );
    } catch {
      lastState = "renderer unavailable during recovery";
    }
    if (
      lastState &&
      typeof lastState === "object" &&
      "ready" in lastState &&
      lastState.ready === true &&
      "pendingRequests" in lastState &&
      lastState.pendingRequests === 0
    ) {
      quietSamples += 1;
      if (quietSamples >= 2) return;
    } else {
      quietSamples = 0;
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(
    `renderer did not settle its sidecar API requests: ${JSON.stringify(lastState)}`,
  );
}

async function crashRendererAndVerifyRecovery(
  window: BrowserWindow,
  host: DesktopHost,
): Promise<void> {
  /** 强制结束真实 renderer，并从仍存活的 main 进程访问 sidecar 私有接口。 */
  await new Promise<void>((resolve, reject) => {
    const timeout = setTimeout(
      () => reject(new Error("renderer crash event timed out")),
      5_000,
    );
    window.webContents.once("render-process-gone", () => {
      clearTimeout(timeout);
      resolve();
    });
    window.webContents.forcefullyCrashRenderer();
  });
  const context = host.context();
  const response = await fetch(`${context.transport.baseUrl}/api/desktop/resources`, {
    headers: {
      Authorization: `Bearer ${context.transport.credential}`,
    },
    signal: AbortSignal.timeout(2_000),
  });
  if (!response.ok) {
    throw new Error(`sidecar resource probe returned ${response.status}`);
  }
  const envelope = (await response.json()) as {
    readonly success?: boolean;
    readonly data?: { readonly draining?: boolean };
  };
  if (!envelope.success || envelope.data?.draining !== false) {
    throw new Error("sidecar stopped serving before Host shutdown began");
  }
  await waitForRendererReady(window);
}

async function verifyWindowResidency(
  window: BrowserWindow,
  host: DesktopHost,
): Promise<void> {
  /** 真实关窗后确认窗口和 sidecar 仍存活，再恢复同一个 renderer。 */
  window.close();
  await waitForWindowVisibility(window, false);
  if (window.isDestroyed()) {
    throw new Error("closing the macOS window destroyed the renderer");
  }
  const context = host.context();
  const response = await fetch(`${context.transport.baseUrl}/api/health`, {
    headers: { Authorization: `Bearer ${context.transport.credential}` },
    signal: AbortSignal.timeout(2_000),
  });
  if (!response.ok) {
    throw new Error(`hidden-window sidecar probe returned ${response.status}`);
  }
  focusDesktopWindow(window);
  await waitForWindowVisibility(window, true);
}

async function waitForWindowVisibility(
  window: BrowserWindow,
  expectedVisible: boolean,
): Promise<void> {
  /** 等待 Electron 完成异步 show/hide，避免按调用返回时机误判。 */
  const deadline = Date.now() + 2_000;
  while (Date.now() < deadline) {
    if (!window.isDestroyed() && window.isVisible() === expectedVisible) return;
    await new Promise((resolve) => setTimeout(resolve, 25));
  }
  throw new Error(`window visibility did not become ${expectedVisible}`);
}
