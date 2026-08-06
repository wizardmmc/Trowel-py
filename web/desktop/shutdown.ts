/** 执行 sidecar cooperative drain、信号升级和最终资源核验。 */

import type { SidecarStartOptions, StartedSidecar } from "./sidecar";
import {
  countLiveSnapshotResources,
  signalSnapshotResources,
  type ExitMarkerInput,
  writeExitMarker,
} from "./resourceCleanup";

const COOPERATIVE_TIMEOUT_MS = 8_000;
const TERM_TIMEOUT_MS = 3_000;
const FINAL_TIMEOUT_MS = 1_000;

interface DrainResponse {
  readonly status: "closed" | "needs_reconcile";
}

interface ResourceCountResult {
  readonly remaining: number;
  readonly verified: boolean;
}

export interface SidecarShutdownResult {
  readonly status: "closed" | "needs_reconcile";
  readonly remainingResourceCount: number;
  readonly forced: boolean;
  readonly exitMarkerRecorded: boolean;
}

export type SidecarShutdownReason = ExitMarkerInput["exitReason"];

export interface SidecarShutdownDependencies {
  readonly requestDrain: (
    running: StartedSidecar,
    timeoutMs: number,
  ) => Promise<DrainResponse>;
  readonly signalResources: (
    options: SidecarStartOptions,
    signal: "SIGTERM" | "SIGKILL",
  ) => Promise<void>;
  readonly countResources: (options: SidecarStartOptions) => Promise<number>;
  readonly recordExit: (
    options: SidecarStartOptions,
    marker: ExitMarkerInput,
  ) => Promise<void>;
  readonly delay: (milliseconds: number) => Promise<void>;
  readonly now: () => Date;
}

const DEFAULT_DEPENDENCIES: SidecarShutdownDependencies = {
  requestDrain,
  signalResources: signalSnapshotResources,
  countResources: countLiveSnapshotResources,
  recordExit: writeExitMarker,
  delay: (milliseconds) =>
    new Promise((resolve) => setTimeout(resolve, milliseconds)),
  now: () => new Date(),
};

export async function shutdownSidecar(
  running: StartedSidecar,
  options: SidecarStartOptions,
  reason: SidecarShutdownReason = "app_exit",
  dependencies: SidecarShutdownDependencies = DEFAULT_DEPENDENCIES,
): Promise<SidecarShutdownResult> {
  /** cooperative 阶段失败也继续按快照收敛，不能把 HTTP 失败当作退出完成。 */
  const requestedAt = dependencies.now();
  let processExited = false;
  void running.process.exited.then(() => {
    processExited = true;
  });
  const cooperative = await dependencies
    .requestDrain(running, COOPERATIVE_TIMEOUT_MS)
    .then((report) => report.status === "closed")
    .catch(() => false);

  running.process.signal("SIGTERM");
  await dependencies.signalResources(options, "SIGTERM");
  if (cooperative) {
    await Promise.race([
      running.process.exited,
      dependencies.delay(TERM_TIMEOUT_MS),
    ]);
  } else {
    await dependencies.delay(TERM_TIMEOUT_MS);
  }
  let exited = processExited;
  let resourceCount = await countResources(options, dependencies);
  const forced =
    !cooperative ||
    !exited ||
    !resourceCount.verified ||
    resourceCount.remaining > 0;

  if (!exited || !resourceCount.verified || resourceCount.remaining > 0) {
    if (!exited) running.process.signal("SIGKILL");
    await dependencies.signalResources(options, "SIGKILL");
    await dependencies.delay(FINAL_TIMEOUT_MS);
    exited = processExited;
    resourceCount = await countResources(options, dependencies);
  }

  const remainingResourceCount =
    resourceCount.remaining +
    (resourceCount.verified ? 0 : 1) +
    (exited ? 0 : 1);
  const status = remainingResourceCount === 0 ? "closed" : "needs_reconcile";
  await dependencies.recordExit(options, {
    exitReason: reason,
    requestedAt: requestedAt.toISOString(),
    completedAt: dependencies.now().toISOString(),
    exitMode: forced ? "forced" : "cooperative",
    processTreeResult: status,
    remainingResourceCount,
  });
  return {
    status,
    remainingResourceCount,
    forced,
    exitMarkerRecorded: true,
  };
}

async function countResources(
  options: SidecarStartOptions,
  dependencies: SidecarShutdownDependencies,
): Promise<ResourceCountResult> {
  /** 快照不可读时保留一个未知项，防止 Host 把无法核验写成干净退出。 */
  try {
    return {
      remaining: await dependencies.countResources(options),
      verified: true,
    };
  } catch {
    return { remaining: 0, verified: false };
  }
}

async function requestDrain(
  running: StartedSidecar,
  timeoutMs: number,
): Promise<DrainResponse> {
  const response = await fetch(
    `${running.transport.baseUrl}/api/desktop/drain`,
    {
      method: "POST",
      headers: { Authorization: `Bearer ${running.transport.credential}` },
      signal: AbortSignal.timeout(timeoutMs),
    },
  );
  if (!response.ok) throw new Error(`drain returned ${response.status}`);
  const envelope = (await response.json()) as {
    readonly success?: boolean;
    readonly data?: DrainResponse;
  };
  if (!envelope.success || !envelope.data) {
    throw new Error("drain response did not contain data");
  }
  return envelope.data;
}
