/** 为桌面行为 E2E 创建隔离 HOME、数据根、Vite 和退出核验。 */

import { spawn } from "node:child_process";
import {
  access,
  mkdir,
  mkdtemp,
  readFile,
  rm,
  writeFile,
} from "node:fs/promises";
import { createServer } from "node:net";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { buildDevelopmentDesktopEnvironment } from "../../web/desktop/devEnvironment.mjs";
import { createRuntimeFixtures } from "./runtime-fixtures.mjs";
import { startModelCatalogServer } from "./model-catalog.mjs";
import { terminateVerifiedProcessGroups } from "./process-groups.mjs";
import { redactIdentity } from "./resource-assertions.mjs";

const supportDirectory = path.dirname(fileURLToPath(import.meta.url));
export const repositoryRoot = path.resolve(supportDirectory, "../..");
export const webRoot = path.join(repositoryRoot, "web");

/**
 * 持有一次 Electron E2E 的全部临时路径和外部进程。
 *
 * 场景驱动只依赖这里公开的启动参数和核验方法，不负责猜测数据目录或清理进程。
 */
export class DesktopTestEnvironment {
  /**
   * 保存一次尚未启动的隔离运行环境。
   *
   * @param {object} options 本次运行的已分配路径和端口。
   * @param {string} options.root 临时运行根目录。
   * @param {string} options.rendererUrl Vite renderer 地址。
   * @param {Record<string, string>} options.electronEnv Electron 主进程环境变量。
   * @param {import("node:child_process").ChildProcess} options.vite Vite 子进程。
   */
  constructor({ root, rendererUrl, electronEnv, vite, modelCatalog }) {
    this.root = root;
    this.webRoot = webRoot;
    this.rendererUrl = rendererUrl;
    this.electronEnv = electronEnv;
    this.vite = vite;
    this.modelCatalog = modelCatalog;
    this.homeDirectory = path.join(root, "home");
    this.dataDirectory = path.join(root, "data");
    this.logDirectory = path.join(root, "logs");
    this.electronDirectory = path.join(root, "electron");
    this.workspaceDirectory = path.join(root, "workspace");
    this.exitMarkerPath = path.join(this.dataDirectory, "resource-exit.json");
    this.resourceSnapshotPath = path.join(
      this.dataDirectory,
      "resource-lifecycle.json",
    );
    this.dataRootIdentity = redactIdentity(path.resolve(this.dataDirectory));
    this.appInstanceIdentity = null;
    this.serviceDescriptorPath = path.join(root, "agent-service.json");
    this.electronBinary = resolveElectronBinary();
  }

  /** 等待最终退出 marker，并拒绝任何未归零资源。 */
  async verifyCleanExit(timeoutMs = 20_000) {
    const marker = await waitForJson(this.exitMarkerPath, timeoutMs);
    if (marker.status !== "closed" || marker.remaining_resource_count !== 0) {
      throw new Error(`desktop resources did not close: ${JSON.stringify(marker)}`);
    }
    return marker;
  }

  /** 读取当前原子资源快照；缺失或写坏时拒绝把未知状态当作归零。 */
  async readResourceSnapshot() {
    return waitForJson(this.resourceSnapshotPath, 5_000);
  }

  /** 保存已通过 readiness 的 descriptor 实例身份，供后续快照终止权限核验。 */
  registerAppInstanceIdentity(identity) {
    if (typeof identity !== "string" || !identity) {
      throw new Error("desktop app instance identity is required");
    }
    this.appInstanceIdentity = identity;
  }

  /** 结束 Vite，并按调用方选择保留或删除本次隔离目录。 */
  async dispose({ preserve = false } = {}) {
    const errors = [];
    try {
      await terminateChildProcess(this.vite);
    } catch (error) {
      errors.push(error);
    }
    try {
      await this.modelCatalog.close();
    } catch (error) {
      errors.push(error);
    }
    try {
      await terminateSnapshotProcessGroups(
        this.dataDirectory,
        this.appInstanceIdentity,
        this.dataRootIdentity,
      );
    } catch (error) {
      errors.push(error);
    }
    if (!preserve) {
      try {
        await rm(this.root, { recursive: true, force: true });
      } catch (error) {
        errors.push(error);
      }
    }
    if (errors.length > 0) throw errors[0];
  }
}

/** 创建一次可由 Playwright 或 WebdriverIO 共同消费的桌面环境。 */
export async function createDesktopTestEnvironment(label) {
  if (process.platform !== "darwin") {
    throw new Error("Trowel Electron behavior E2E requires macOS");
  }
  const root = await mkdtemp(path.join(os.tmpdir(), `trowel-e2e-${label}-`));
  let vite = null;
  let modelCatalog = null;
  try {
    const homeDirectory = path.join(root, "home");
    const dataDirectory = path.join(root, "data");
    const logDirectory = path.join(root, "logs");
    const electronDirectory = path.join(root, "electron");
    const memoryDirectory = path.join(root, "memory");
    const runtimeStateDirectory = path.join(root, "runtime-state");
    const serviceDescriptorPath = path.join(root, "agent-service.json");
    const workspaceDirectory = path.join(root, "workspace");
    await Promise.all(
      [homeDirectory, dataDirectory, logDirectory, electronDirectory, memoryDirectory, runtimeStateDirectory, workspaceDirectory].map(
        (directory) => mkdir(directory, { recursive: true }),
      ),
    );
    await writeFile(
      path.join(dataDirectory, "config.toml"),
      `[memory]\nroot = ${JSON.stringify(memoryDirectory)}\n`,
      "utf8",
    );
    const rendererPort = await reservePort();
    const rendererUrl = `http://127.0.0.1:${rendererPort}`;
    vite = spawn(
      path.join(webRoot, "node_modules", ".bin", "vite"),
      ["--host", "127.0.0.1", "--port", String(rendererPort), "--strictPort"],
      {
        cwd: webRoot,
        stdio: ["ignore", "pipe", "pipe"],
        env: {
          ...process.env,
          TROWEL_DESKTOP_SERVICE_FILE: serviceDescriptorPath,
        },
      },
    );
    await waitForUrl(rendererUrl, vite);
    const runtimes = await createRuntimeFixtures(
      root,
      process.env.PATH ?? "/usr/bin:/bin",
    );
    modelCatalog = await startModelCatalogServer();
    const electronEnv = buildDevelopmentDesktopEnvironment(process.env, {
      HOME: homeDirectory,
      TROWEL_PROJECT_ROOT: repositoryRoot,
      TROWEL_RENDERER_URL: rendererUrl,
      TROWEL_DESKTOP_SERVICE_FILE: serviceDescriptorPath,
      TROWEL_DESKTOP_DATA_MODE: "isolated-dev",
      TROWEL_DESKTOP_DATA_DIR: dataDirectory,
      TROWEL_DESKTOP_LOG_DIR: logDirectory,
      TROWEL_ELECTRON_USER_DATA_DIR: electronDirectory,
      TROWEL_AGENT_SESSIONS_PATH: path.join(dataDirectory, "agent_sessions.json"),
      TROWEL_WORKSPACES_PATH: path.join(dataDirectory, "workspaces.db"),
      TROWEL_RUNTIME_DISCOVERY_DISABLED: "1",
      TROWEL_DESKTOP_E2E: "1",
      TROWEL_E2E_RUNTIME_STATE: runtimeStateDirectory,
      PATH: runtimes.path,
    });
    return new DesktopTestEnvironment({
      root,
      rendererUrl,
      electronEnv,
      vite,
      modelCatalog,
    });
  } catch (error) {
    await rollbackDesktopEnvironmentCreation({
      root,
      vite,
      modelCatalog,
      setupError: error,
    });
  }
}

/**
 * 环境创建中途失败时继续尝试全部清理，并同时报告原始失败与清理失败。
 *
 * @param {object} options 创建阶段已经取得的资源。
 * @param {string} options.root 本次隔离根。
 * @param {import("node:child_process").ChildProcess|null} options.vite 已启动的 Vite。
 * @param {{close: () => Promise<void>}|null} options.modelCatalog 模型目录服务。
 * @param {unknown} options.setupError 触发回滚的原始创建错误。
 */
export async function rollbackDesktopEnvironmentCreation({
  root,
  vite,
  modelCatalog,
  setupError,
}) {
  const errors = [setupError];
  if (vite) {
    try {
      await terminateChildProcess(vite);
    } catch (error) {
      errors.push(error);
    }
  }
  if (modelCatalog) {
    try {
      await modelCatalog.close();
    } catch (error) {
      errors.push(error);
    }
  }
  try {
    await rm(root, { recursive: true, force: true });
  } catch (error) {
    errors.push(error);
  }
  if (errors.length === 1) throw setupError;
  throw new AggregateError(errors, "desktop environment setup rollback failed");
}

/** 暂时把隔离变量放入当前进程，供只继承 parent env 的驱动使用。 */
export function installProcessEnvironment(environment) {
  const previous = new Map();
  for (const [name, value] of Object.entries(environment)) {
    previous.set(name, process.env[name]);
    process.env[name] = value;
  }
  return () => {
    for (const [name, value] of previous) {
      if (value === undefined) delete process.env[name];
      else process.env[name] = value;
    }
  };
}

/** 返回 web 工程固定 Electron 的 macOS 可执行文件。 */
function resolveElectronBinary() {
  return path.join(
    webRoot,
    "node_modules",
    "electron",
    "dist",
    "Electron.app",
    "Contents",
    "MacOS",
    "Electron",
  );
}

/** 从内核预留端口，关闭监听后交给 Vite 使用。 */
async function reservePort() {
  return new Promise((resolve, reject) => {
    const server = createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      if (!address || typeof address === "string") {
        server.close(() => reject(new Error("could not reserve renderer port")));
        return;
      }
      server.close((error) => (error ? reject(error) : resolve(address.port)));
    });
  });
}

/** 等待 Vite 可访问，并在 Vite 提前退出时立即失败。 */
async function waitForUrl(url, child, timeoutMs = 15_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (child.exitCode !== null) {
      throw new Error(`Vite exited before readiness with code ${child.exitCode}`);
    }
    try {
      const response = await fetch(url, { signal: AbortSignal.timeout(500) });
      if (response.ok) return;
    } catch {
      // Vite 尚未监听时继续等待。
    }
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  throw new Error("Vite did not become ready before the E2E deadline");
}

/** 等待原子 JSON 文件可读。 */
async function waitForJson(filePath, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      return JSON.parse(await readFile(filePath, "utf8"));
    } catch {
      await new Promise((resolve) => setTimeout(resolve, 50));
    }
  }
  throw new Error(`timed out waiting for ${path.basename(filePath)}`);
}

/** 等待子进程退出，避免临时目录删除后 Vite 仍持有文件。 */
async function waitForChildExit(child, timeoutMs) {
  if (child.exitCode !== null || child.signalCode !== null) return;
  await new Promise((resolve, reject) => {
    const onExit = () => {
      clearTimeout(deadline);
      resolve();
    };
    const deadline = setTimeout(() => {
      child.off("exit", onExit);
      reject(new Error("child process exit timed out"));
    }, timeoutMs);
    child.once("exit", onExit);
  });
}

/** 先请求子进程退出，超时后强制结束并再次等待到真正退出。 */
export async function terminateChildProcess(
  child,
  { graceMs = 5_000, finalMs = 5_000 } = {},
) {
  if (child.exitCode !== null || child.signalCode !== null) return;
  child.kill("SIGTERM");
  try {
    await waitForChildExit(child, graceMs);
  } catch {
    child.kill("SIGKILL");
    await waitForChildExit(child, finalMs);
  }
}

/** 只终止本次隔离资源快照登记的进程组，收敛驱动建连前失败的遗留进程。 */
async function terminateSnapshotProcessGroups(
  dataDirectory,
  expectedAppInstanceIdentity,
  expectedDataRootIdentity,
) {
  if (!expectedAppInstanceIdentity) return;
  let snapshot;
  try {
    snapshot = JSON.parse(
      await readFile(path.join(dataDirectory, "resource-lifecycle.json"), "utf8"),
    );
  } catch {
    return;
  }
  await terminateVerifiedProcessGroups(snapshot, {
    expectedAppInstanceIdentity,
    expectedDataRootIdentity,
  });
}

/** 在启动前确认桌面构建和 Electron 二进制存在。 */
export async function assertDesktopBuildReady() {
  await Promise.all([
    access(path.join(webRoot, "desktop-dist", "desktop", "main.js")),
    access(resolveElectronBinary()),
  ]);
}
