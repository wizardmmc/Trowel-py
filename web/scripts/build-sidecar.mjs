/** 用仓库锁定的 PyInstaller 构建不依赖系统 Python 的 onedir sidecar。 */

import { spawn } from "node:child_process";
import { chmod, rename, rm } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const webRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const projectRoot = path.resolve(webRoot, "..");
const releaseRoot = path.join(webRoot, ".release");
const temporaryOutput = path.join(releaseRoot, "trowel-sidecar");
const finalOutput = path.join(releaseRoot, "sidecar");
const managedEnvironment = path.join(releaseRoot, "python-env");
const python = process.env.TROWEL_BUILD_PYTHON
  ? path.resolve(process.env.TROWEL_BUILD_PYTHON)
  : path.join(managedEnvironment, "bin", "python");

/** 运行子命令并把退出信号、非零状态转换成明确的构建失败。 */
function run(command, args, environment = process.env) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      cwd: projectRoot,
      env: environment,
      stdio: "inherit",
    });
    child.once("error", reject);
    child.once("exit", (code, signal) => {
      if (code === 0) resolve();
      else reject(new Error(`${command} exited code=${code} signal=${signal}`));
    });
  });
}

if (!process.env.TROWEL_BUILD_PYTHON) {
  await run(
    process.env.TROWEL_BUILD_UV ?? "uv",
    [
      "sync",
      "--locked",
      "--group",
      "dev",
      "--python",
      "3.13.12",
      "--managed-python",
    ],
    { ...process.env, UV_PROJECT_ENVIRONMENT: managedEnvironment },
  );
}
await rm(temporaryOutput, { recursive: true, force: true });
await rm(finalOutput, { recursive: true, force: true });
await run(python, [
  "-m",
  "PyInstaller",
  "--noconfirm",
  "--clean",
  "--onedir",
  "--name",
  "trowel-sidecar",
  "--collect-all",
  "trowel_py",
  "--distpath",
  releaseRoot,
  "--workpath",
  path.join(releaseRoot, "pyinstaller-build"),
  "--specpath",
  path.join(releaseRoot, "pyinstaller-spec"),
  path.join(projectRoot, "trowel_py", "desktop", "packaged_entrypoint.py"),
]);
await rename(temporaryOutput, finalOutput);
await chmod(path.join(finalOutput, "trowel-sidecar"), 0o755);
