/** 启动随机端口 Vite、Electron 和隔离的真实 Python sidecar 开发链。 */

import { spawn } from "node:child_process";
import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { createServer } from "node:net";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import electron from "electron";

const webRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const projectRoot = path.resolve(webRoot, "..");
const rendererSmoke = process.argv.includes("--smoke");
const diagnosticSmoke = process.argv.includes("--diagnostic-smoke");
const singleInstanceSmoke = process.argv.includes("--single-instance-smoke");
const sharedServiceSmoke = process.argv.includes("--shared-service-smoke");
const smoke =
  rendererSmoke || diagnosticSmoke || singleInstanceSmoke || sharedServiceSmoke;
const tempRoot = smoke
  ? await mkdtemp(path.join(os.tmpdir(), "trowel-desktop-smoke-"))
  : null;
const runtimeRoot =
  tempRoot ?? await mkdtemp(path.join(os.tmpdir(), "trowel-desktop-dev-"));
const serviceDescriptorPath = path.join(runtimeRoot, "agent-service.json");
const rendererPort = await reservePort();
const rendererUrl = `http://127.0.0.1:${rendererPort}`;

if (tempRoot) {
  const dataDirectory = path.join(tempRoot, "data");
  const memoryDirectory = path.join(tempRoot, "memory");
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
    },
  },
);
let desktop = null;
let interrupted = false;

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
  const desktopEnvironment = {
    ...process.env,
    TROWEL_PROJECT_ROOT: projectRoot,
    TROWEL_RENDERER_URL: rendererUrl,
    TROWEL_DESKTOP_SERVICE_FILE: serviceDescriptorPath,
    ...(rendererSmoke ? { TROWEL_DESKTOP_SMOKE: "1" } : {}),
    ...(diagnosticSmoke
      ? {
          TROWEL_DESKTOP_DIAGNOSTIC_SMOKE: "1",
          TROWEL_PYTHON_EXECUTABLE: path.join(tempRoot, "missing-python"),
        }
      : {}),
    ...(singleInstanceSmoke
      ? { TROWEL_DESKTOP_SINGLE_INSTANCE_SMOKE: "1" }
      : {}),
    ...(tempRoot
      ? {
          TROWEL_DESKTOP_DATA_DIR: path.join(tempRoot, "data"),
          TROWEL_DESKTOP_LOG_DIR: path.join(tempRoot, "logs"),
          TROWEL_ELECTRON_USER_DATA_DIR: path.join(tempRoot, "electron"),
          TROWEL_AGENT_SESSIONS_PATH: path.join(
            tempRoot,
            "data",
            "agent_sessions.json",
          ),
          TROWEL_WORKSPACES_PATH: path.join(
            tempRoot,
            "data",
            "workspaces.db",
          ),
        }
      : {}),
  };
  desktop = spawn(electron, [webRoot], {
    cwd: webRoot,
    stdio: "inherit",
    env: desktopEnvironment,
  });
  const primaryExit = childExit(desktop);
  if (singleInstanceSmoke) {
    const lifecycleLog = path.join(tempRoot, "logs", "desktop-host.log");
    await waitForFileText(lifecycleLog, '"event":"sidecar_ready"');
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
  process.exitCode = interrupted || expectedDesktopStop ? 0 : exitCode;
} finally {
  process.removeListener("SIGINT", handleTerminationSignal);
  process.removeListener("SIGTERM", handleTerminationSignal);
  vite.kill("SIGTERM");
  await rm(runtimeRoot, { recursive: true, force: true });
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
