/** 按 Python 快照中的启动身份核验并收敛本实例独立进程组。 */

import { execFile } from "node:child_process";
import { createHash } from "node:crypto";
import { readFile, rename, writeFile } from "node:fs/promises";
import path from "node:path";
import { promisify } from "node:util";
import type { SidecarStartOptions } from "./sidecar";

const execFileAsync = promisify(execFile);

interface SnapshotResource {
  readonly resource_kind?: unknown;
  readonly state?: unknown;
  readonly pid?: unknown;
  readonly process_group?: unknown;
  readonly process_start_identity?: unknown;
}

interface ResourceSnapshot {
  readonly version: number;
  readonly app_instance_id: string;
  readonly resources: readonly SnapshotResource[];
}

interface ProcessIdentity {
  readonly pid: number;
  readonly processGroup: number;
  readonly startIdentity: string;
}

export interface ResourceCountDependencies {
  readonly readSnapshot: (
    options: SidecarStartOptions,
  ) => Promise<ResourceSnapshot | null>;
  readonly inspectProcess: (pid: number) => Promise<ProcessIdentity | null>;
  readonly processGroupAlive: (processGroup: number) => boolean;
}

const DEFAULT_COUNT_DEPENDENCIES: ResourceCountDependencies = {
  readSnapshot: readCurrentSnapshot,
  inspectProcess,
  processGroupAlive,
};

export async function signalSnapshotResources(
  options: SidecarStartOptions,
  signal: "SIGTERM" | "SIGKILL",
): Promise<void> {
  /** 每次发信号前重读进程表，PID 复用或进程组变化时跳过。 */
  const snapshot = await readCurrentSnapshot(options);
  if (!snapshot) return;
  const groups = new Set<number>();
  for (const resource of snapshot.resources) {
    if (resource.state === "closed" || resource.resource_kind === "sidecar_process_group") {
      continue;
    }
    const expected = snapshotIdentity(resource);
    if (!expected) continue;
    const current = await inspectProcess(expected.pid);
    if (!current || !sameIdentity(current, expected)) continue;
    groups.add(expected.processGroup);
  }
  const hostIdentity = await inspectProcess(globalThis.process.pid);
  for (const processGroup of groups) {
    if (processGroup <= 1 || processGroup === hostIdentity?.processGroup) continue;
    try {
      globalThis.process.kill(-processGroup, signal);
    } catch (error) {
      if (errorCode(error) !== "ESRCH") throw error;
    }
  }
}

export async function countLiveSnapshotResources(
  options: SidecarStartOptions,
  dependencies: ResourceCountDependencies = DEFAULT_COUNT_DEPENDENCIES,
): Promise<number> {
  /** 根身份匹配或登记进程组仍活着都继续计数；无法核验时不能假报归零。 */
  const snapshot = await dependencies.readSnapshot(options);
  if (!snapshot) throw new Error("resource snapshot unavailable");
  let live = 0;
  for (const resource of snapshot.resources) {
    if (resource.state === "closed" || resource.resource_kind === "sidecar_process_group") {
      continue;
    }
    const expected = snapshotIdentity(resource);
    if (!expected) {
      live += 1;
      continue;
    }
    const current = await dependencies.inspectProcess(expected.pid);
    if (
      (current && sameIdentity(current, expected)) ||
      dependencies.processGroupAlive(expected.processGroup)
    ) {
      live += 1;
    }
  }
  return live;
}

export async function writeExitMarker(
  options: SidecarStartOptions,
  status: "closed" | "needs_reconcile",
  remainingResourceCount: number,
): Promise<void> {
  /** 原子记录 Host 最终核验结果，供下次启动和诊断页区分干净退出。 */
  const finalPath = path.join(options.dataDirectory, "resource-exit.json");
  const temporaryPath = `${finalPath}.tmp`;
  await writeFile(
    temporaryPath,
    `${JSON.stringify({
      version: 1,
      app_instance_id: redactIdentity(options.instanceId),
      status,
      remaining_resource_count: remainingResourceCount,
      updated_at: new Date().toISOString(),
    })}\n`,
    { encoding: "utf8", mode: 0o600 },
  );
  await rename(temporaryPath, finalPath);
}

async function readCurrentSnapshot(
  options: SidecarStartOptions,
): Promise<ResourceSnapshot | null> {
  try {
    const raw = await readFile(
      path.join(options.dataDirectory, "resource-lifecycle.json"),
      "utf8",
    );
    const value = JSON.parse(raw) as Partial<ResourceSnapshot>;
    if (
      value.version !== 1 ||
      value.app_instance_id !== redactIdentity(options.instanceId) ||
      !Array.isArray(value.resources)
    ) {
      return null;
    }
    return value as ResourceSnapshot;
  } catch {
    return null;
  }
}

function snapshotIdentity(resource: SnapshotResource): ProcessIdentity | null {
  if (
    typeof resource.pid !== "number" ||
    typeof resource.process_group !== "number" ||
    typeof resource.process_start_identity !== "string"
  ) {
    return null;
  }
  return {
    pid: resource.pid,
    processGroup: resource.process_group,
    startIdentity: resource.process_start_identity,
  };
}

async function inspectProcess(pid: number): Promise<ProcessIdentity | null> {
  if (pid <= 0) return null;
  try {
    const { stdout } = await execFileAsync("ps", [
      "-o",
      "pgid=",
      "-o",
      "lstart=",
      "-o",
      "comm=",
      "-p",
      String(pid),
    ]);
    const fields = stdout.trim().split(/\s+/);
    if (fields.length < 7) return null;
    const processGroup = Number(fields.shift());
    if (!Number.isInteger(processGroup)) return null;
    return {
      pid,
      processGroup,
      startIdentity: createHash("sha256").update(fields.join(" ")).digest("hex"),
    };
  } catch {
    return null;
  }
}

function processGroupAlive(processGroup: number): boolean {
  /** 信号 0 只探测进程组是否存在，不改变目标状态。 */
  if (globalThis.process.platform === "win32" || processGroup <= 1) return false;
  try {
    globalThis.process.kill(-processGroup, 0);
    return true;
  } catch (error) {
    return errorCode(error) === "EPERM";
  }
}

function sameIdentity(current: ProcessIdentity, expected: ProcessIdentity): boolean {
  return (
    current.pid === expected.pid &&
    current.processGroup === expected.processGroup &&
    current.startIdentity === expected.startIdentity
  );
}

function redactIdentity(value: string): string {
  return createHash("sha256").update(value).digest("hex").slice(0, 20);
}

function errorCode(error: unknown): string | undefined {
  if (!error || typeof error !== "object" || !("code" in error)) return undefined;
  return typeof error.code === "string" ? error.code : undefined;
}
