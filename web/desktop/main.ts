/** 组装单实例 Electron Host、Python sidecar、窗口和桌面 IPC 生命周期。 */

import path from "node:path";
import { pathToFileURL } from "node:url";
import { randomBytes, randomUUID } from "node:crypto";
import { app, type BrowserWindow } from "electron";
import { DesktopHost } from "./host";
import { registerDesktopIpc } from "./ipc";
import { createLifecycleLogger } from "./lifecycleLogger";
import {
  removeAgentServiceDescriptor,
  writeAgentServiceDescriptor,
} from "./serviceDescriptor";
import { createDesktopWindow, focusDesktopWindow } from "./window";

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
const singleInstanceSmoke =
  process.env.TROWEL_DESKTOP_SINGLE_INSTANCE_SMOKE === "1";
const serviceDescriptorPath = process.env.TROWEL_DESKTOP_SERVICE_FILE;

if (process.env.TROWEL_ELECTRON_USER_DATA_DIR) {
  app.setPath("userData", path.resolve(process.env.TROWEL_ELECTRON_USER_DATA_DIR));
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
  const dataDirectory = path.resolve(
    process.env.TROWEL_DESKTOP_DATA_DIR ??
      (app.isPackaged ? app.getPath("userData") : projectRoot),
  );
  const logDirectory = path.resolve(
    process.env.TROWEL_DESKTOP_LOG_DIR ?? app.getPath("logs"),
  );
  const logLifecycle = createLifecycleLogger(logDirectory);

  await app.whenReady();
  let mainWindow: BrowserWindow | null = null;
  let removeIpcHandlers: (() => void) | null = null;
  let host: DesktopHost | null = null;

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
    mainWindow.on("closed", () => {
      mainWindow = null;
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
    logLifecycle("renderer_loaded");
    if (rendererSmoke || singleInstanceSmoke) {
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
    }
  };
  const loadDiagnostics = async () => {
    await removeAgentService();
    const window = ensureWindow();
    await window.loadFile(diagnosticEntry);
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
        state?.title === "后台服务没有启动" &&
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
      executable: resolvePythonExecutable(projectRoot),
      cwd: projectRoot,
      dataDirectory,
      logDirectory,
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
    rendererUrl,
    diagnosticUrl,
  });

  app.on("second-instance", () => {
    focusDesktopWindow(mainWindow);
    logLifecycle("second_instance");
    if (singleInstanceSmoke) {
      console.log("TROWEL_DESKTOP_SINGLE_INSTANCE_OK");
      app.quit();
    }
  });
  app.on("activate", () => {
    if (mainWindow) {
      focusDesktopWindow(mainWindow);
      return;
    }
    if (host.diagnostics().status === "ready") void loadRenderer();
    else void loadDiagnostics();
  });
  app.on("window-all-closed", () => {
    if (process.platform !== "darwin") app.quit();
  });
  app.once("before-quit", () => {
    logLifecycle("host_quit");
    host?.stop();
    void removeAgentService();
    removeIpcHandlers?.();
    removeIpcHandlers = null;
  });

  logLifecycle("sidecar_starting");
  await host.start();
  const state = host.diagnostics();
  logLifecycle(
    state.status === "ready" ? "sidecar_ready" : "sidecar_failed",
    state.category,
  );
  if (rendererSmoke && state.status !== "ready") {
    process.exitCode = 1;
    app.quit();
  }
}

function resolvePythonExecutable(root: string): string {
  if (process.env.TROWEL_PYTHON_EXECUTABLE) {
    return path.resolve(process.env.TROWEL_PYTHON_EXECUTABLE);
  }
  if (app.isPackaged) {
    return path.join(process.resourcesPath, "sidecar", "python", "bin", "python");
  }
  return path.join(root, ".venv", "bin", "python");
}

function rendererOrigin(url: string): string {
  const parsed = new URL(url);
  return parsed.protocol === "file:" ? "null" : parsed.origin;
}

async function waitForRendererReady(window: BrowserWindow): Promise<void> {
  const deadline = Date.now() + 10_000;
  let lastState: unknown = null;
  while (Date.now() < deadline) {
    lastState = await window.webContents.executeJavaScript(
      `({
        bridgeType: typeof window.trowelDesktop,
        ready: window.__TROWEL_RENDERER_READY__ === true,
        rootText: document.querySelector('#root')?.textContent?.slice(0, 120) ?? ''
      })`,
      true,
    );
    if (
      lastState &&
      typeof lastState === "object" &&
      "ready" in lastState &&
      lastState.ready === true
    ) {
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(
    `renderer did not complete its sidecar API request: ${JSON.stringify(lastState)}`,
  );
}
