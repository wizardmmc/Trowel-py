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
  type Session,
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
import type { SidecarShutdownResult } from "./shutdown";
import { createDesktopTelemetrySender } from "./telemetryPort";
import { TelemetryBatcher } from "../shared/telemetry-batcher";
import { DesktopRuntimeTelemetry } from "./runtimeTelemetry";
import {
  countLiveSnapshotResources,
  writeExitMarker,
} from "./resourceCleanup";

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
const settingsSmoke = process.env.TROWEL_DESKTOP_SETTINGS_SMOKE === "1";
const diagnosticSmoke = process.env.TROWEL_DESKTOP_DIAGNOSTIC_SMOKE === "1";
const residencySmoke = process.env.TROWEL_DESKTOP_RESIDENCY_SMOKE === "1";
const singleInstanceSmoke =
  process.env.TROWEL_DESKTOP_SINGLE_INSTANCE_SMOKE === "1";
const rendererCrashSmoke =
  process.env.TROWEL_DESKTOP_RENDERER_CRASH_SMOKE === "1";
const agentTransportSmoke =
  process.env.TROWEL_DESKTOP_AGENT_TRANSPORT_SMOKE === "1";
const serviceDescriptorPath = process.env.TROWEL_DESKTOP_SERVICE_FILE;

configureSafeStorageForSmoke(
  app.commandLine,
  rendererSmoke ||
    settingsSmoke ||
    diagnosticSmoke ||
    residencySmoke ||
    singleInstanceSmoke ||
    rendererCrashSmoke ||
    agentTransportSmoke,
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
  void startDesktopApplication().catch((error) => {
    console.error(
      "Trowel desktop host failed before diagnostics were available.",
      error,
    );
    app.exit(1);
  });
}

async function startDesktopApplication(): Promise<void> {
  const applicationStartedAt = new Date();
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
  const sidecarOptions = {
    command: resolveSidecarLaunchCommand(projectRoot),
    cwd: projectRoot,
    dataDirectory,
    logDirectory,
    dataMode: desktopDataMode,
    instanceId,
    credential,
    expectedAppVersion: app.getVersion(),
    rendererOrigin: rendererOrigin(rendererUrl),
  };

  await app.whenReady();
  await configureRendererSession(session.defaultSession);
  const agentStreamObservation = agentTransportSmoke
    ? observeAgentStreams(session.defaultSession)
    : null;
  let mainWindow: BrowserWindow | null = null;
  let removeIpcHandlers: (() => void) | null = null;
  let host: DesktopHost | null = null;
  let currentPage: DesktopPage = null;
  let previousRendererCrashAt: number | null = null;
  let rendererCrashSmokeStarted = false;
  let finalQuit = false;
  let quitPromise: Promise<void> | null = null;
  let statusTray: Tray | null = null;
  let desktopTelemetry: DesktopRuntimeTelemetry | null = null;
  let sidecarReadyCount = 0;
  let firstScreenRecorded = false;

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
        desktopTelemetry?.recordRendererCrash(new Date(now));
        void desktopTelemetry?.flush();
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
      desktopTelemetry?.recordWindowClose(new Date());
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
    } else if (settingsSmoke) {
      await window.loadFile(rendererEntry, { query: { tool: "settings" } });
    } else {
      await window.loadFile(rendererEntry);
    }
    currentPage = "renderer";
    logLifecycle("renderer_loaded");
    const rendererReady = waitForRendererReady(window);
    void rendererReady
      .then(() => {
        if (firstScreenRecorded) return;
        firstScreenRecorded = true;
        desktopTelemetry?.recordFirstScreen(new Date());
      })
      .catch((error) => {
        logLifecycle(
          "renderer_ready_observation_failed",
          error instanceof Error ? error.name : "unknown",
        );
      });
    const runRendererCrashSmoke =
      rendererCrashSmoke && !rendererCrashSmokeStarted;
    if (
      rendererSmoke ||
      settingsSmoke ||
      residencySmoke ||
      singleInstanceSmoke ||
      rendererCrashSmoke ||
      agentTransportSmoke
    ) {
      try {
        await rendererReady;
      } catch (error) {
        console.error("TROWEL_DESKTOP_SMOKE_FAILED", error);
        throw error;
      }
      if (rendererSmoke) {
        if (!host) throw new Error("desktop host is not initialized");
        await verifyTelemetrySmoke(host);
        console.log("TROWEL_DESKTOP_SMOKE_OK");
        app.quit();
      }
      if (settingsSmoke) {
        try {
          await verifySettingsSmoke(window);
          console.log("TROWEL_DESKTOP_SETTINGS_SMOKE_OK");
        } catch (error) {
          process.exitCode = 1;
          console.error("TROWEL_DESKTOP_SETTINGS_SMOKE_FAILED", error);
        }
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
      if (agentTransportSmoke) {
        try {
          if (!agentStreamObservation) {
            throw new Error("Agent stream observation was not initialized");
          }
          await verifyAgentTransportSmoke(window, agentStreamObservation);
          console.log("TROWEL_DESKTOP_AGENT_TRANSPORT_SMOKE_OK");
        } catch (error) {
          process.exitCode = 1;
          console.error("TROWEL_DESKTOP_AGENT_TRANSPORT_SMOKE_FAILED", error);
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

  host = new DesktopHost(sidecarOptions, {
    loadRenderer,
    loadDiagnostics,
    onSidecarReady: (running) => {
      const batcher = new TelemetryBatcher({
        sourceComponent: "electron",
        send: createDesktopTelemetrySender(running.transport),
      });
      desktopTelemetry = new DesktopRuntimeTelemetry(
        batcher,
        applicationStartedAt,
      );
      desktopTelemetry.recordSidecarReady(new Date());
      if (sidecarReadyCount > 0) {
        desktopTelemetry.recordSidecarRestart(new Date());
      }
      sidecarReadyCount += 1;
    },
    onUnexpectedExit: (exit) => {
      // 资源快照和退出标记由 DesktopHost 的统一 shutdown 链路写入。
      logLifecycle(
        "sidecar_unexpected_exit",
        exit.signal ?? String(exit.code ?? "unknown"),
      );
    },
  });
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
    const exitRequestedAt = new Date();
    quitPromise = (async () => {
      try {
        await desktopTelemetry?.drain(250);
      } catch {
        // 遥测排空失败不占用应用退出链。
      }
      let shutdownResult: SidecarShutdownResult = {
        status: "needs_reconcile",
        remainingResourceCount: 1,
        forced: true,
        exitMarkerRecorded: false,
      };
      try {
        const result = await host?.stop();
        if (result) shutdownResult = result;
        logLifecycle("host_drain_finished", result?.status ?? "closed");
      } catch (error) {
        logLifecycle(
          "host_drain_failed",
          error instanceof Error ? error.name : "unknown",
        );
      }
      if (!shutdownResult.exitMarkerRecorded) {
        const remainingResourceCount = await countLiveSnapshotResources(
          sidecarOptions,
        ).catch(() => Math.max(shutdownResult.remainingResourceCount, 1));
        const processTreeResult =
          remainingResourceCount === 0 ? "closed" : "needs_reconcile";
        try {
          await writeExitMarker(sidecarOptions, {
            exitReason: "app_exit",
            requestedAt: exitRequestedAt.toISOString(),
            completedAt: new Date().toISOString(),
            exitMode:
              shutdownResult.forced || processTreeResult === "needs_reconcile"
                ? "forced"
                : "cooperative",
            processTreeResult,
            remainingResourceCount,
          });
        } catch (error) {
          logLifecycle(
            "host_exit_marker_failed",
            error instanceof Error ? error.name : "unknown",
          );
        }
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

/** 等待设置页真实 DTO 落地，并核对桌面平台专属布局与路径能力。 */
async function verifySettingsSmoke(window: BrowserWindow): Promise<void> {
  const deadline = Date.now() + 20_000;
  let lastState: unknown = null;
  while (Date.now() < deadline) {
    try {
      lastState = await window.webContents.executeJavaScript(
        `(() => {
          const workspace = document.querySelector('.settings-workspace');
          const sidebar = document.querySelector('.settings-sidebar');
          const dragRegion = document.querySelector('.settings-drag-region');
          const labels = Array.from(
            document.querySelectorAll('.settings-sidebar__item strong'),
            (element) => element.textContent?.trim() ?? '',
          );
          const revealButtons = Array.from(
            document.querySelectorAll('.settings-path-row button[aria-label^="打开"]'),
          );
          return {
            tool: new URL(location.href).searchParams.get('tool'),
            platform: document.documentElement.dataset.platform ?? null,
            workspace: workspace !== null,
            labels,
            pathRows: document.querySelectorAll('.settings-path-row').length,
            hasEnabledReveal: revealButtons.some((element) => !element.disabled),
            secondaryWidth: sidebar?.getBoundingClientRect().width ?? 0,
            dragRegionHeight: dragRegion?.getBoundingClientRect().height ?? 0,
            dragRegionMode: dragRegion
              ? getComputedStyle(dragRegion).getPropertyValue('-webkit-app-region')
              : '',
            horizontalOverflow:
              document.documentElement.scrollWidth > document.documentElement.clientWidth,
          };
        })()`,
        true,
      );
    } catch {
      lastState = "settings renderer unavailable";
    }
    if (
      lastState &&
      typeof lastState === "object" &&
      "tool" in lastState &&
      lastState.tool === "settings" &&
      "platform" in lastState &&
      lastState.platform === "desktop" &&
      "workspace" in lastState &&
      lastState.workspace === true &&
      "labels" in lastState &&
      Array.isArray(lastState.labels) &&
      lastState.labels.join("|") ===
        "存储与路径|模型连接|后台任务|Agent 默认|连接诊断|关于" &&
      "pathRows" in lastState &&
      lastState.pathRows === 7 &&
      "hasEnabledReveal" in lastState &&
      lastState.hasEnabledReveal === true &&
      "secondaryWidth" in lastState &&
      lastState.secondaryWidth === 216 &&
      "dragRegionHeight" in lastState &&
      lastState.dragRegionHeight === 48 &&
      "dragRegionMode" in lastState &&
      lastState.dragRegionMode === "drag" &&
      "horizontalOverflow" in lastState &&
      lastState.horizontalOverflow === false
    ) {
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(
    `settings renderer did not reach the desktop contract: ${JSON.stringify(lastState)}`,
  );
}

interface AgentStreamObservation {
  /** renderer 发起应用级事件流的累计次数。 */
  eventStarts: number;
  /** renderer 发起旧 session 级消息流的累计次数。 */
  messageStarts: number;
  /** renderer 发起旧 session 级事件流的累计次数。 */
  sessionEventStarts: number;
  /** 尚未完成的应用级事件流请求。 */
  readonly activeEventIds: Set<number>;
  /** 尚未完成的旧 session 级消息流请求。 */
  readonly activeMessageIds: Set<number>;
  /** 尚未完成的旧 session 级事件流请求。 */
  readonly activeSessionEventIds: Set<number>;
}

/** 在 Electron 网络栈边界记录 Agent 长连接，不读取请求正文或凭据。 */
function observeAgentStreams(browserSession: Session): AgentStreamObservation {
  const observation: AgentStreamObservation = {
    eventStarts: 0,
    messageStarts: 0,
    sessionEventStarts: 0,
    activeEventIds: new Set<number>(),
    activeMessageIds: new Set<number>(),
    activeSessionEventIds: new Set<number>(),
  };
  const filter = {
    urls: ["http://127.0.0.1:*/*", "http://localhost:*/*"],
  };

  browserSession.webRequest.onBeforeRequest(filter, (details, callback) => {
    const kind = agentStreamKind(details.url);
    if (kind === "application_events") {
      observation.eventStarts += 1;
      observation.activeEventIds.add(details.id);
    } else if (kind === "session_events") {
      observation.sessionEventStarts += 1;
      observation.activeSessionEventIds.add(details.id);
    } else if (kind === "messages") {
      observation.messageStarts += 1;
      observation.activeMessageIds.add(details.id);
    }
    callback({ cancel: false });
  });
  const markFinished = (details: { readonly id: number; readonly url: string }) => {
    const kind = agentStreamKind(details.url);
    if (kind === "application_events") {
      observation.activeEventIds.delete(details.id);
    } else if (kind === "session_events") {
      observation.activeSessionEventIds.delete(details.id);
    } else if (kind === "messages") {
      observation.activeMessageIds.delete(details.id);
    }
  };
  browserSession.webRequest.onCompleted(filter, markFinished);
  browserSession.webRequest.onErrorOccurred(filter, markFinished);
  return observation;
}

/** 把网络请求路径归类为新应用流、旧 session 流或普通请求。 */
function agentStreamKind(
  url: string,
): "application_events" | "session_events" | "messages" | null {
  const pathname = new URL(url).pathname;
  if (pathname === "/api/agent/events") return "application_events";
  if (
    pathname.startsWith("/api/agent/sessions/") &&
    pathname.endsWith("/events")
  ) {
    return "session_events";
  }
  if (
    pathname.startsWith("/api/agent/sessions/") &&
    pathname.endsWith("/messages")
  ) {
    return "messages";
  }
  return null;
}

/**
 * 从真实 Electron renderer 穿过 Chromium HTTP/1.1 栈验证单 SSE 与 20/5 容量。
 *
 * 所有会话都使用隔离目录和只保持进程存活的可控 runtime。返回结果只含计数和耗时，
 * 不把临时 session ID、工作目录或输入正文写入日志。
 */
async function verifyAgentTransportSmoke(
  window: BrowserWindow,
  observation: AgentStreamObservation,
): Promise<void> {
  const result = (await window.webContents.executeJavaScript(
    `(async () => {
      const workdir = ${JSON.stringify(projectRoot)};
      const context = await window.trowelDesktop.getContext();
      const authHeaders = { Authorization: "Bearer " + context.transport.credential };
      const api = async (path, options = {}, timeoutMs = 3000) => {
        const headers = { ...authHeaders, ...(options.headers || {}) };
        return fetch(context.transport.baseUrl + path, {
          ...options,
          headers,
          signal: AbortSignal.timeout(timeoutMs),
        });
      };
      const json = async (response) => {
        const body = await response.json();
        if (!response.ok) throw new Error("request returned " + response.status);
        return body.data;
      };
      const waitUntil = async (read, accept, label, timeoutMs = 8000) => {
        const deadline = Date.now() + timeoutMs;
        let value;
        while (Date.now() < deadline) {
          value = await read();
          if (accept(value)) return value;
          await new Promise((resolve) => setTimeout(resolve, 100));
        }
        throw new Error(
          "condition timed out: " + label + "; last=" + JSON.stringify(value),
        );
      };
      const createBody = JSON.stringify({
        runtime: "claude_code",
        workdir,
        permission_mode: "bypassPermissions",
        memory_enabled: false,
        profile_enabled: false,
        self_enabled: false,
      });
      const sessions = await Promise.all(
        Array.from({ length: 20 }, async () => {
          const response = await api("/api/agent/sessions", {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              "X-Trowel-Request-Id": crypto.randomUUID(),
            },
            body: createBody,
          });
          return json(response);
        }),
      );
      await Promise.all(
        sessions.map((session) =>
          api("/api/agent/sessions/" + session.session_id + "/title", {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ title: "Smoke" }),
          }).then(json),
        ),
      );
      const turn = (sessionId, text) =>
        api("/api/agent/sessions/" + sessionId + "/turns", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text }),
        }, 5000);
      const active = () =>
        api("/api/agent/sessions/active").then(json).then((data) => data.sessions);
      await waitUntil(
        async () => {
          const rows = await active();
          return { count: rows.length };
        },
        (facts) => facts.count === 20,
        "twenty registered sessions",
      );
      for (let offset = 0; offset < sessions.length; offset += 5) {
        const warmed = await Promise.all(
          sessions.slice(offset, offset + 5).map((session) =>
            turn(session.session_id, "warm"),
          ),
        );
        if (warmed.some((response) => !response.ok)) {
          throw new Error("session warm-up was not accepted");
        }
        await waitUntil(
          async () => {
            const rows = await active();
            return { running: rows.filter((row) => row.running).length };
          },
          (facts) => facts.running === 0,
          "warm-up batch completed",
        );
      }
      const connectedFacts = await waitUntil(
        async () => {
          const rows = await active();
          return {
            count: rows.length,
            connected: rows.filter((row) => row.connected).length,
          };
        },
        (facts) => facts.count === 20 && facts.connected === 20,
        "twenty connected sessions",
      );
      document.querySelector('button[aria-label="Agent"]')?.click();
      window.dispatchEvent(new Event("focus"));
      const rendered = await waitUntil(
        async () => document.querySelectorAll(".cc-multibar__item").length,
        (count) => count >= 7,
        "at least seven rendered sessions",
      );

      const firstFive = sessions.slice(0, 5);
      const accepted = await Promise.all(
        firstFive.map((session) => turn(session.session_id, "hold")),
      );
      if (accepted.some((response) => response.status !== 200)) {
        throw new Error("five turns were not accepted");
      }
      await waitUntil(
        async () => {
          const rows = await active();
          return { running: rows.filter((row) => row.running).length };
        },
        (facts) => facts.running === 5,
        "five running sessions",
      );

      const sixthStartedAt = performance.now();
      const sixth = await turn(sessions[5].session_id, "hold");
      const sixthElapsedMs = performance.now() - sixthStartedAt;
      if (sixth.status !== 409 || sixthElapsedMs >= 2000) {
        throw new Error("sixth turn did not receive a bounded capacity rejection");
      }
      const overflowStartedAt = performance.now();
      const overflow = await api("/api/agent/sessions", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Trowel-Request-Id": crypto.randomUUID(),
        },
        body: createBody,
      });
      const overflowElapsedMs = performance.now() - overflowStartedAt;
      if (overflow.status !== 409 || overflowElapsedMs >= 2000) {
        throw new Error("twenty-first session did not receive a bounded capacity rejection");
      }

      const readsStartedAt = performance.now();
      const reads = await Promise.all([
        api("/api/agent/session-defaults"),
        ...sessions.slice(0, 6).map((session) =>
          api("/api/agent/sessions/" + session.session_id + "/history"),
        ),
      ]);
      const readsElapsedMs = performance.now() - readsStartedAt;
      if (reads.some((response) => !response.ok) || readsElapsedMs >= 2000) {
        throw new Error("seven concurrent reads exceeded the renderer budget");
      }

      await Promise.all(
        firstFive.map((session) =>
          api("/api/agent/sessions/" + session.session_id + "/interrupt", {
            method: "POST",
          }, 8000).then(json),
        ),
      );
      await waitUntil(
        async () => {
          const rows = await active();
          return { running: rows.filter((row) => row.running).length };
        },
        (facts) => facts.running === 0,
        "interrupt released running capacity",
      );
      const replacement = await turn(sessions[5].session_id, "hold");
      if (!replacement.ok) throw new Error("capacity was not released after interrupt");
      await waitUntil(
        async () => {
          const rows = await active();
          return { running: rows.filter((row) => row.running).length };
        },
        (facts) => facts.running === 1,
        "replacement turn running",
      );

      const closed = await Promise.all(
        sessions.map((session) =>
          api("/api/agent/sessions/" + session.session_id, {
            method: "DELETE",
          }, 12000),
        ),
      );
      if (closed.some((response) => !response.ok)) {
        throw new Error("session cleanup failed");
      }
      await waitUntil(
        async () => ({ count: (await active()).length }),
        (facts) => facts.count === 0,
        "all sessions closed",
      );
      return {
        connected: connectedFacts.connected,
        rendered,
        running: firstFive.length,
        reads: reads.length,
        sixthElapsedMs,
        overflowElapsedMs,
        readsElapsedMs,
      };
    })()`,
    true,
  )) as {
    readonly connected: number;
    readonly rendered: number;
    readonly running: number;
    readonly reads: number;
    readonly sixthElapsedMs: number;
    readonly overflowElapsedMs: number;
    readonly readsElapsedMs: number;
  };

  if (
    result.connected !== 20 ||
    result.rendered < 7 ||
    result.running !== 5 ||
    result.reads !== 7 ||
    observation.eventStarts < 1 ||
    observation.activeEventIds.size !== 1 ||
    observation.sessionEventStarts !== 0 ||
    observation.activeSessionEventIds.size !== 0 ||
    observation.messageStarts !== 0 ||
    observation.activeMessageIds.size !== 0
  ) {
    throw new Error(
      `Agent transport facts did not match the 20/5 single-stream contract: ${JSON.stringify(
        {
          connected: result.connected,
          rendered: result.rendered,
          running: result.running,
          reads: result.reads,
          eventStarts: observation.eventStarts,
          activeEvents: observation.activeEventIds.size,
          sessionEventStarts: observation.sessionEventStarts,
          activeSessionEvents: observation.activeSessionEventIds.size,
          messageStarts: observation.messageStarts,
          activeMessages: observation.activeMessageIds.size,
        },
      )}`,
    );
  }
}

async function verifyTelemetrySmoke(host: DesktopHost): Promise<void> {
  /** 通过真实实例凭据验证批量接收和 Host 退出前 drain。 */
  const context = host.context();
  const batcher = new TelemetryBatcher({
    sourceComponent: "electron",
    send: createDesktopTelemetrySender(context.transport),
    flushIntervalMs: 10,
    idFactory: () => `batch-smoke-${randomUUID()}`,
  });
  const startedAt = new Date();
  const endedAt = new Date(startedAt.getTime() + 1);
  batcher.recordSpan({
    trace_id: randomBytes(16).toString("hex"),
    span_id: randomBytes(8).toString("hex"),
    parent_span_id: null,
    started_at: startedAt.toISOString(),
    ended_at: endedAt.toISOString(),
    component: "electron",
    operation: "desktop.start",
    status: "ok",
    runtime: null,
    model: null,
    session_ref: null,
    call_ref: null,
    attributes: { quality: "reliable", sampled: true },
    links: [],
  });
  const report = await batcher.drain(2_000);
  if (!report.drained || report.dropped !== 0) {
    throw new Error(`desktop telemetry drain failed: ${JSON.stringify(report)}`);
  }
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
