/** 在隔离用户目录和数据目录中启动完整 Trowel.app 并检查退出事实。 */

import { spawn } from "node:child_process";
import { access, mkdir, mkdtemp, readFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { isAcceptedPackagedAppExit } from "./smoke-packaged-exit.mjs";
import { resolvePackagedSmokeDataDirectory } from "./smoke-packaged-paths.mjs";

const webRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const executable = process.env.TROWEL_PACKAGED_APP_EXECUTABLE
  ? path.resolve(process.env.TROWEL_PACKAGED_APP_EXECUTABLE)
  : path.join(
      webRoot,
      "out",
      "Trowel-darwin-arm64",
      "Trowel.app",
      "Contents",
      "MacOS",
      "Trowel",
    );
const residencySmoke = process.argv.includes("--residency");
const defaultPathsSmoke = process.argv.includes("--default-paths");
const preserveSmokeRoot = process.env.TROWEL_PACKAGED_SMOKE_PRESERVE === "1";
if (defaultPathsSmoke && process.env.CI !== "true") {
  throw new Error("default path smoke is restricted to an ephemeral CI user");
}
const smokeRoot = process.env.TROWEL_PACKAGED_SMOKE_ROOT
  ? path.resolve(process.env.TROWEL_PACKAGED_SMOKE_ROOT)
  : await mkdtemp(path.join(os.tmpdir(), "trowel-packaged-smoke-"));
const homeDirectory = defaultPathsSmoke
  ? os.homedir()
  : path.join(smokeRoot, "home");
const dataDirectory = resolvePackagedSmokeDataDirectory({
  defaultPathsSmoke,
  homeDirectory,
  smokeRoot,
});
const logDirectory = defaultPathsSmoke
  ? path.join(homeDirectory, "Library", "Logs", "Trowel")
  : path.join(smokeRoot, "logs");
const electronDirectory = path.join(smokeRoot, "electron");
const initialDirectories = defaultPathsSmoke
  ? [homeDirectory]
  : [homeDirectory, dataDirectory, logDirectory, electronDirectory];
for (const directory of initialDirectories) {
  await mkdir(directory, { recursive: true });
}

/** 运行带 60 秒上限的安装包主进程并收集有界输出。 */
function runPackagedApp() {
  return new Promise((resolve, reject) => {
    const child = spawn(executable, [], {
      cwd: smokeRoot,
      env: {
        HOME: homeDirectory,
        PATH: "/usr/bin:/bin:/usr/sbin:/sbin",
        TROWEL_RUNTIME_DISCOVERY_DISABLED: "1",
        ...(residencySmoke
          ? { TROWEL_DESKTOP_RESIDENCY_SMOKE: "1" }
          : { TROWEL_DESKTOP_SMOKE: "1" }),
        ...(defaultPathsSmoke
          ? {}
          : {
              TROWEL_DESKTOP_DATA_DIR: dataDirectory,
              TROWEL_DESKTOP_LOG_DIR: logDirectory,
              TROWEL_ELECTRON_USER_DATA_DIR: electronDirectory,
            }),
      },
      stdio: ["ignore", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => {
      stdout = `${stdout}${chunk}`.slice(-128 * 1024);
    });
    child.stderr.on("data", (chunk) => {
      stderr = `${stderr}${chunk}`.slice(-128 * 1024);
    });
    const timeout = setTimeout(() => {
      child.kill("SIGTERM");
      reject(new Error(`packaged smoke timed out; root=${smokeRoot}`));
    }, 60_000);
    child.once("error", (error) => {
      clearTimeout(timeout);
      reject(error);
    });
    child.once("exit", (code, signal) => {
      clearTimeout(timeout);
      resolve({ code, signal, stdout, stderr });
    });
  });
}

let passed = false;
try {
  const result = await runPackagedApp();
  const expectedMarker = residencySmoke
    ? "TROWEL_DESKTOP_RESIDENCY_SMOKE_OK"
    : "TROWEL_DESKTOP_SMOKE_OK";
  if (!isAcceptedPackagedAppExit(result) || !result.stdout.includes(expectedMarker)) {
    throw new Error(
      `packaged smoke failed code=${result.code} signal=${result.signal}\n${result.stdout}\n${result.stderr}`,
    );
  }
  const sidecarLog = await readFile(path.join(logDirectory, "trowel.log"), "utf8");
  for (const forbidden of [
    "FOREIGN KEY constraint failed",
    "UNIQUE constraint failed",
    "Unhandled exception",
    "HTTP Request:",
  ]) {
    if (sidecarLog.includes(forbidden)) {
      throw new Error(`packaged smoke log contains ${forbidden}; root=${smokeRoot}`);
    }
  }
  const exitMarker = JSON.parse(
    await readFile(path.join(dataDirectory, "resource-exit.json"), "utf8"),
  );
  if (exitMarker.status !== "closed" || exitMarker.remaining_resource_count !== 0) {
    throw new Error(`packaged smoke did not close all resources; root=${smokeRoot}`);
  }
  await access(path.join(dataDirectory, "trowel.db"));
  const forbiddenHomeEntries = defaultPathsSmoke
    ? [".trowel"]
    : [".trowel", ".codex", ".claude"];
  for (const name of forbiddenHomeEntries) {
    try {
      await access(path.join(homeDirectory, name));
      throw new Error(`packaged smoke unexpectedly created ${name}; root=${smokeRoot}`);
    } catch (error) {
      if (error?.code !== "ENOENT") throw error;
    }
  }
  passed = true;
  console.log(
    residencySmoke
      ? "TROWEL_PACKAGED_RESIDENCY_SMOKE_OK"
      : "TROWEL_PACKAGED_APP_SMOKE_OK",
  );
} finally {
  if (passed && !preserveSmokeRoot) {
    await rm(smokeRoot, { recursive: true, force: true });
  }
  else console.error(`Packaged smoke files kept at ${smokeRoot}`);
}
