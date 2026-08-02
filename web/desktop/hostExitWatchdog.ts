/** 在 macOS 原生退出卡住时终止已经完成资源清理的 Electron Host。 */

import { spawn, type SpawnOptions } from "node:child_process";

const MACOS_WATCHDOG_SCRIPT = `
target_pid=$1
delay_seconds=$2
sleep "$delay_seconds"
current_parent=$(ps -o ppid= -p $$ | tr -d '[:space:]')
if [ "$current_parent" = "$target_pid" ]; then
  kill -KILL "$target_pid"
fi
`;

interface WatchdogChild {
  once(event: "spawn", listener: () => void): this;
  once(event: "error", listener: (error: Error) => void): this;
  unref(): void;
}

export type SpawnWatchdog = (
  command: string,
  args: string[],
  options: SpawnOptions,
) => WatchdogChild;

export interface HostExitWatchdogOptions {
  readonly platform?: NodeJS.Platform;
  readonly parentPid?: number;
  readonly delaySeconds?: number;
  readonly spawnWatchdog?: SpawnWatchdog;
}

const spawnWatchdog: SpawnWatchdog = (command, args, options) =>
  spawn(command, args, options);

export async function armHostExitWatchdog(
  options: HostExitWatchdogOptions = {},
): Promise<boolean> {
  /** 只在 macOS 启用；子进程会复核自己仍由目标 PID 托管，避免误杀复用 PID。 */
  const platform = options.platform ?? process.platform;
  if (platform !== "darwin") return false;

  const parentPid = options.parentPid ?? process.pid;
  const delaySeconds = options.delaySeconds ?? 2;
  if (!Number.isInteger(parentPid) || parentPid <= 1) {
    throw new Error("exit watchdog requires a valid parent PID");
  }
  if (!Number.isFinite(delaySeconds) || delaySeconds <= 0) {
    throw new Error("exit watchdog requires a positive delay");
  }

  const child = (options.spawnWatchdog ?? spawnWatchdog)(
    "/bin/sh",
    [
      "-c",
      MACOS_WATCHDOG_SCRIPT,
      "trowel-exit-watchdog",
      String(parentPid),
      String(delaySeconds),
    ],
    { detached: true, stdio: "ignore" },
  );
  await new Promise<void>((resolve, reject) => {
    child.once("spawn", resolve);
    child.once("error", reject);
  });
  child.unref();
  return true;
}
