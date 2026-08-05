/** 启动随机端口 Vite、Electron 和可选隔离数据的 Python sidecar 开发链。 */

import { spawn } from "node:child_process";
import {
  mkdtemp,
  mkdir,
  readFile,
  readdir,
  rm,
  writeFile,
} from "node:fs/promises";
import { createServer } from "node:net";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import electron from "electron";
import {
  buildDevelopmentDesktopEnvironment,
  resolveDevelopmentDataMode,
} from "./devEnvironment.mjs";
import { createHoldingClaudePath } from "../scripts/fake-claude-smoke.mjs";

const webRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const projectRoot = path.resolve(webRoot, "..");
const rendererSmoke = process.argv.includes("--smoke");
const settingsSmoke = process.argv.includes("--settings-smoke");
const diagnosticSmoke = process.argv.includes("--diagnostic-smoke");
const singleInstanceSmoke = process.argv.includes("--single-instance-smoke");
const sidecarHangSmoke = process.argv.includes("--sidecar-hang-smoke");
const rendererCrashSmoke = process.argv.includes("--renderer-crash-smoke");
const sharedServiceSmoke = process.argv.includes("--shared-service-smoke");
const agentTransportSmoke = process.argv.includes("--agent-transport-smoke");
const readOnlyInspection = process.argv.includes("--observe");
const smoke =
  rendererSmoke ||
  settingsSmoke ||
  diagnosticSmoke ||
  singleInstanceSmoke ||
  sidecarHangSmoke ||
  rendererCrashSmoke ||
  sharedServiceSmoke ||
  agentTransportSmoke;
const developmentDataMode = resolveDevelopmentDataMode(process.argv, {
  usesTemporaryDataRoot: smoke,
});
const tempRoot = smoke
  ? await mkdtemp(path.join(os.tmpdir(), "trowel-desktop-smoke-"))
  : null;
const runtimeRoot =
  tempRoot ?? await mkdtemp(path.join(os.tmpdir(), "trowel-desktop-dev-"));
const privateDataRoot = tempRoot ?? (readOnlyInspection ? runtimeRoot : null);
const serviceDescriptorPath = path.join(runtimeRoot, "agent-service.json");
const smokePath = agentTransportSmoke
  ? await createHoldingClaudePath(runtimeRoot, process.env.PATH ?? "/usr/bin:/bin")
  : process.env.PATH;
const rendererPort = await reservePort();
const rendererUrl = `http://127.0.0.1:${rendererPort}${readOnlyInspection ? "/?tool=statistics" : settingsSmoke ? "/?tool=settings" : ""}`;

if (privateDataRoot) {
  const dataDirectory = path.join(privateDataRoot, "data");
  const memoryDirectory = path.join(privateDataRoot, "memory");
  await mkdir(dataDirectory, { recursive: true });
  await writeFile(
    path.join(dataDirectory, "config.toml"),
    `[memory]\nroot = ${JSON.stringify(memoryDirectory)}\n`,
    "utf8",
  );
}

const vite = spawn(
  path.join(webRoot, "node_modules", ".bin", "vite"),
  ["--host", "127.0.0.1", "--port", String(rendererPort), "--strictPort"],
  {
    cwd: webRoot,
    stdio: "inherit",
    env: {
      ...process.env,
      TROWEL_DESKTOP_SERVICE_FILE: serviceDescriptorPath,
      ...(readOnlyInspection ? { VITE_TROWEL_INSPECTION_MODE: "1" } : {}),
    },
  },
);
let desktop = null;
let desktopExitCode = null;
let interrupted = false;
let stoppedSidecarPid = null;
let smokeFailed = false;

/** 终端中断时先结束子进程，让 finally 有机会清理私有 descriptor。 */
function handleTerminationSignal() {
  interrupted = true;
  desktop?.kill("SIGTERM");
  vite.kill("SIGTERM");
}

process.once("SIGINT", handleTerminationSignal);
process.once("SIGTERM", handleTerminationSignal);

try {
  await waitForUrl(rendererUrl, vite);
  const desktopEnvironment = buildDevelopmentDesktopEnvironment(process.env, {
    TROWEL_PROJECT_ROOT: projectRoot,
    TROWEL_RENDERER_URL: rendererUrl,
    TROWEL_DESKTOP_SERVICE_FILE: serviceDescriptorPath,
    TROWEL_DESKTOP_DATA_MODE: developmentDataMode,
    ...(readOnlyInspection
      ? {
          TROWEL_DESKTOP_INSPECTION_ONLY: "1",
          TROWEL_DESKTOP_READ_DATA_DIR:
            process.env.TROWEL_DESKTOP_READ_DATA_DIR ??
            defaultCanonicalDataDirectory(),
        }
      : {}),
    ...(rendererSmoke ? { TROWEL_DESKTOP_SMOKE: "1" } : {}),
    ...(settingsSmoke ? { TROWEL_DESKTOP_SETTINGS_SMOKE: "1" } : {}),
    ...(diagnosticSmoke
      ? {
          TROWEL_DESKTOP_DIAGNOSTIC_SMOKE: "1",
          TROWEL_PYTHON_EXECUTABLE: path.join(tempRoot, "missing-python"),
        }
      : {}),
    ...(singleInstanceSmoke || sidecarHangSmoke
      ? { TROWEL_DESKTOP_SINGLE_INSTANCE_SMOKE: "1" }
      : {}),
    ...(rendererCrashSmoke
      ? { TROWEL_DESKTOP_RENDERER_CRASH_SMOKE: "1" }
      : {}),
    ...(agentTransportSmoke
      ? {
          TROWEL_DESKTOP_AGENT_TRANSPORT_SMOKE: "1",
          TROWEL_RUNTIME_DISCOVERY_DISABLED: "1",
          PATH: smokePath,
        }
      : {}),
    ...(privateDataRoot
      ? {
          TROWEL_DESKTOP_DATA_DIR: path.join(privateDataRoot, "data"),
          TROWEL_DESKTOP_LOG_DIR: path.join(privateDataRoot, "logs"),
          TROWEL_ELECTRON_USER_DATA_DIR: path.join(privateDataRoot, "electron"),
          TROWEL_AGENT_SESSIONS_PATH: path.join(
            privateDataRoot,
            "data",
            "agent_sessions.json",
          ),
          TROWEL_WORKSPACES_PATH: path.join(
            privateDataRoot,
            "data",
            "workspaces.db",
          ),
        }
      : {}),
  });
  desktop = spawn(electron, [webRoot], {
    cwd: webRoot,
    stdio: "inherit",
    env: desktopEnvironment,
  });
  const primaryExit = childExit(desktop);
  if (singleInstanceSmoke || sidecarHangSmoke) {
    const lifecycleLog = path.join(tempRoot, "logs", "desktop-host.log");
    await waitForFileText(lifecycleLog, '"event":"sidecar_ready"');
    if (sidecarHangSmoke) {
      const snapshotPath = path.join(
        tempRoot,
        "data",
        "resource-lifecycle.json",
      );
      const snapshot = await waitForJson(snapshotPath);
      const sidecar = snapshot.resources.find(
        (resource) => resource.resource_kind === "sidecar_process_group",
      );
      if (!Number.isInteger(sidecar?.pid)) {
        throw new Error("Sidecar PID was not published in the resource snapshot.");
      }
      stoppedSidecarPid = sidecar.pid;
      process.kill(stoppedSidecarPid, "SIGSTOP");
    }
    const second = spawn(electron, [webRoot], {
      cwd: webRoot,
      stdio: "inherit",
      env: desktopEnvironment,
    });
    const secondExit = await childExit(second);
    if (secondExit !== 0) throw new Error("Second Electron instance did not exit cleanly.");
  }
  let expectedDesktopStop = false;
  if (sharedServiceSmoke) {
    const lifecycleLog = path.join(tempRoot, "logs", "desktop-host.log");
    await waitForFileText(lifecycleLog, '"event":"renderer_loaded"');
    await verifySharedAgentService(rendererUrl, serviceDescriptorPath);
    console.log("TROWEL_DESKTOP_SHARED_SERVICE_SMOKE_OK");
    expectedDesktopStop = true;
    desktop.kill("SIGTERM");
  }
  const exitCode = await primaryExit;
  desktopExitCode = exitCode;
  if (rendererSmoke) {
    if (exitCode !== 0) throw new Error("Electron telemetry smoke did not exit cleanly.");
    await verifyTelemetryDatabase(
      path.join(tempRoot, "data", "telemetry.db"),
    );
    const marker = await waitForJson(
      path.join(tempRoot, "data", "resource-exit.json"),
    );
    if (marker.status !== "closed" || marker.remaining_resource_count !== 0) {
      throw new Error(
        `Telemetry smoke did not end with a clean resource marker: ${JSON.stringify(marker)}`,
      );
    }
  }
  if (settingsSmoke) {
    if (exitCode !== 0) throw new Error("Electron settings smoke did not exit cleanly.");
    const marker = await waitForJson(
      path.join(tempRoot, "data", "resource-exit.json"),
    );
    if (marker.status !== "closed" || marker.remaining_resource_count !== 0) {
      throw new Error(
        `Settings smoke did not end with a clean resource marker: ${JSON.stringify(marker)}`,
      );
    }
  }
  if (sidecarHangSmoke) {
    if (exitCode !== 0) throw new Error("Primary Electron instance did not exit cleanly.");
    if (stoppedSidecarPid !== null && processAlive(stoppedSidecarPid)) {
      throw new Error("Stopped sidecar survived Host shutdown escalation.");
    }
    const marker = await waitForJson(
      path.join(tempRoot, "data", "resource-exit.json"),
    );
    if (marker.status !== "closed" || marker.remaining_resource_count !== 0) {
      throw new Error(`Host did not verify a clean exit: ${JSON.stringify(marker)}`);
    }
    console.log("TROWEL_DESKTOP_SIDECAR_HANG_SMOKE_OK");
  }
  if (rendererCrashSmoke) {
    if (exitCode !== 0) throw new Error("Electron exited after renderer crash with an error.");
    const marker = await waitForJson(
      path.join(tempRoot, "data", "resource-exit.json"),
    );
    if (marker.status !== "closed" || marker.remaining_resource_count !== 0) {
      throw new Error(
        `Renderer crash did not end with a clean resource marker: ${JSON.stringify(marker)}`,
      );
    }
    console.log("TROWEL_DESKTOP_RENDERER_CRASH_SMOKE_OK");
  }
  if (agentTransportSmoke) {
    if (exitCode !== 0) {
      throw new Error("Electron Agent transport smoke did not exit cleanly.");
    }
    const marker = await waitForJson(
      path.join(tempRoot, "data", "resource-exit.json"),
    );
    if (marker.status !== "closed" || marker.remaining_resource_count !== 0) {
      throw new Error(
        `Agent transport smoke did not close all resources: ${JSON.stringify(marker)}`,
      );
    }
  }
  process.exitCode = interrupted || expectedDesktopStop ? 0 : exitCode;
  smokeFailed = smoke && process.exitCode !== 0;
} catch (error) {
  smokeFailed = smoke;
  throw error;
} finally {
  process.removeListener("SIGINT", handleTerminationSignal);
  process.removeListener("SIGTERM", handleTerminationSignal);
  vite.kill("SIGTERM");
  if (stoppedSidecarPid !== null && processAlive(stoppedSidecarPid)) {
    try {
      process.kill(stoppedSidecarPid, "SIGCONT");
      process.kill(stoppedSidecarPid, "SIGKILL");
    } catch {
      // 退出核验和进程结束之间可能发生正常竞态。
    }
  }
  if (smokeFailed) {
    await printSmokeDiagnostics(runtimeRoot, desktopExitCode);
  }
  if (process.env.TROWEL_KEEP_DESKTOP_SMOKE_DIR === "1") {
    console.error(`TROWEL_DESKTOP_SMOKE_DIR=${runtimeRoot}`);
  } else {
    await rm(runtimeRoot, { recursive: true, force: true });
  }
}

/** 返回 Electron 在各平台使用的正式 Trowel 业务数据目录。 */
function defaultCanonicalDataDirectory() {
  if (process.platform === "darwin") {
    return path.join(
      os.homedir(),
      "Library",
      "Application Support",
      "Trowel",
      "data",
    );
  }
  if (process.platform === "win32") {
    const appData = process.env.APPDATA ?? path.join(os.homedir(), "AppData", "Roaming");
    return path.join(appData, "Trowel", "data");
  }
  const appData = process.env.XDG_CONFIG_HOME ?? path.join(os.homedir(), ".config");
  return path.join(appData, "Trowel", "data");
}

/** 失败时输出隔离现场中的文件清单和两份生命周期日志。 */
async function printSmokeDiagnostics(root, exitCode) {
  console.error(`TROWEL_DESKTOP_EXIT_CODE=${String(exitCode)}`);
  try {
    const files = await readdir(root, { recursive: true });
    console.error(`TROWEL_DESKTOP_SMOKE_FILES=${JSON.stringify(files.sort())}`);
  } catch (error) {
    console.error("TROWEL_DESKTOP_SMOKE_FILES_UNAVAILABLE", error);
  }
  for (const relativePath of ["logs/desktop-host.log", "logs/trowel.log"]) {
    try {
      const content = await readFile(path.join(root, relativePath), "utf8");
      console.error(`TROWEL_DESKTOP_SMOKE_LOG=${relativePath}\n${content}`);
    } catch {
      console.error(`TROWEL_DESKTOP_SMOKE_LOG_MISSING=${relativePath}`);
    }
  }
}

/** Electron 退出后只读确认被接受的 smoke span 已由 sidecar drain 到数据库。 */
async function verifyTelemetryDatabase(databasePath) {
  const executable =
    process.env.TROWEL_PYTHON_EXECUTABLE ??
    path.join(projectRoot, ".venv", "bin", "python");
  const script = [
    "import pathlib, sqlite3, sys",
    "uri = pathlib.Path(sys.argv[1]).resolve().as_uri() + '?mode=ro'",
    "connection = sqlite3.connect(uri, uri=True)",
    "count = connection.execute(\"SELECT COUNT(*) FROM raw_spans WHERE component='electron' AND operation='desktop.start'\").fetchone()[0]",
    "connection.close()",
    "raise SystemExit(0 if count >= 1 else 1)",
  ].join("; ");
  const verifier = spawn(executable, ["-c", script, databasePath], {
    cwd: projectRoot,
    stdio: ["ignore", "ignore", "inherit"],
    env: process.env,
  });
  const exitCode = await childExit(verifier);
  if (exitCode !== 0) {
    throw new Error("Accepted desktop telemetry was not drained to telemetry.db.");
  }
}

async function waitForJson(filePath, timeoutMs = 15_000) {
  /** 等待 sidecar 或 Host 原子发布 JSON 文件，并容忍临时缺失。 */
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      return JSON.parse(await readFile(filePath, "utf8"));
    } catch {
      await new Promise((resolve) => setTimeout(resolve, 50));
    }
  }
  throw new Error(`Timed out waiting for JSON file: ${filePath}`);
}

function processAlive(pid) {
  /** 用信号 0 检查 PID，权限不足仍视为存活。 */
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    return error?.code === "EPERM";
  }
}

/** 读取 JSON API，并在非成功响应时保留可诊断的状态码。 */
async function requestJson(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) throw new Error(`Request failed: ${response.status} ${url}`);
  return response.json();
}

/** 从 Vite 和 Desktop 私有 transport 两侧交叉验证同一个 SessionHub。 */
async function verifySharedAgentService(rendererUrl, descriptorPath) {
  const descriptor = JSON.parse(await readFile(descriptorPath, "utf8"));
  const createBody = JSON.stringify({
    runtime: "claude_code",
    workdir: projectRoot,
    permission_mode: "default",
    memory_enabled: false,
    profile_enabled: false,
    self_enabled: false,
  });
  const browserCreated = await requestJson(
    `${rendererUrl}/api/agent/sessions`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: createBody,
    },
  );
  const desktopCreated = await requestJson(
    `${descriptor.baseUrl}/api/agent/sessions`,
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${descriptor.credential}`,
        "Content-Type": "application/json",
      },
      body: createBody,
    },
  );
  const browserActive = await requestJson(
    `${rendererUrl}/api/agent/sessions/active`,
  );
  const desktopActive = await requestJson(
    `${descriptor.baseUrl}/api/agent/sessions/active`,
    { headers: { Authorization: `Bearer ${descriptor.credential}` } },
  );
  const expected = new Set([
    browserCreated.data.session_id,
    desktopCreated.data.session_id,
  ]);
  for (const response of [browserActive, desktopActive]) {
    const actual = new Set(response.data.sessions.map((session) => session.session_id));
    if (![...expected].every((sessionId) => actual.has(sessionId))) {
      throw new Error("Web and Desktop did not observe the same Agent sessions.");
    }
  }
}

function reservePort() {
  return new Promise((resolve, reject) => {
    const server = createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      if (!address || typeof address === "string") {
        reject(new Error("Could not reserve a renderer port."));
        return;
      }
      server.close((error) => (error ? reject(error) : resolve(address.port)));
    });
  });
}

async function waitForUrl(url, child) {
  const deadline = Date.now() + 10_000;
  while (Date.now() < deadline) {
    if (child.exitCode !== null) throw new Error("Vite exited before becoming ready.");
    try {
      const response = await fetch(url);
      if (response.ok) return;
    } catch {
      // Vite 尚未监听时继续轮询。
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error("Vite did not become ready in time.");
}

function childExit(child) {
  return new Promise((resolve, reject) => {
    child.once("error", reject);
    child.once("exit", (code) => resolve(code ?? 1));
  });
}

async function waitForFileText(filePath, expected) {
  const deadline = Date.now() + 15_000;
  while (Date.now() < deadline) {
    try {
      if ((await readFile(filePath, "utf8")).includes(expected)) return;
    } catch {
      // Host 尚未创建日志文件时继续轮询。
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(`Desktop lifecycle log did not contain ${expected}.`);
}
