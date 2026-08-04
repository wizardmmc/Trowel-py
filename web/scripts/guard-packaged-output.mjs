/** 在 Electron Forge 删除目标目录前，阻止覆盖仍在运行的本地 App。 */

import { execFileSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

/** 返回 Forge 为指定平台和架构生成的 App bundle 绝对路径。 */
export function packagedAppPath(webRoot, platform, arch) {
  return path.join(webRoot, "out", `Trowel-${platform}-${arch}`, "Trowel.app");
}

/** 从 macOS 进程表中找出命令行引用待覆盖 App bundle 的进程。 */
export function findPackagedOutputProcesses(processTable, appPath) {
  const normalizedAppPath = path.resolve(appPath);
  const matches = [];
  for (const line of processTable.split("\n")) {
    const parsed = line.match(/^\s*(\d+)\s+(.+)$/);
    if (!parsed || !parsed[2].includes(normalizedAppPath)) continue;
    matches.push({ pid: Number(parsed[1]), command: parsed[2] });
  }
  return matches;
}

/** 读取系统进程表并在 Forge 目标仍被使用时抛出可操作错误。 */
export function guardPackagedOutput({ webRoot, platform, arch }) {
  if (platform !== "darwin") return;
  const appPath = packagedAppPath(webRoot, platform, arch);
  const processTable = execFileSync("ps", ["-axo", "pid=,command="], {
    encoding: "utf8",
  });
  const running = findPackagedOutputProcesses(processTable, appPath);
  if (running.length === 0) return;
  const processSummary = running.map(({ pid }) => pid).join(", ");
  throw new Error(
    `打包已停止：${appPath} 仍在运行（PID ${processSummary}）。请先退出这个 web/out App，再重新打包。`,
  );
}

const invokedPath = process.argv[1] ? path.resolve(process.argv[1]) : null;
if (invokedPath && import.meta.url === pathToFileURL(invokedPath).href) {
  const webRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
  guardPackagedOutput({
    webRoot,
    platform: process.env.npm_config_platform || "darwin",
    arch: process.env.npm_config_arch || "arm64",
  });
}
