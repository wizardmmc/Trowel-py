/** 验证 Electron Host 的 8/3/1 秒退出升级语义。 */

// @vitest-environment node

import { expect, it, vi } from "vitest";
import type { RunningSidecar } from "./sidecar";
import { shutdownSidecar, type SidecarShutdownDependencies } from "./shutdown";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

const OPTIONS = {
  command: {
    executable: "/repo/.venv/bin/python",
    args: ["-m", "trowel_py.desktop.sidecar"],
  },
  cwd: "/repo",
  dataDirectory: "/data",
  dataMode: "packaged",
  logDirectory: "/logs",
  instanceId: "instance-123",
  credential: "desktop-secret",
  expectedAppVersion: "0.1.0",
  rendererOrigin: "null",
};

function fixture(killRequired: boolean) {
  const exit = deferred<{ code: number | null; signal: string | null }>();
  const signal = vi.fn((name: "SIGTERM" | "SIGKILL") => {
    if (!killRequired || name === "SIGKILL") {
      exit.resolve({ code: null, signal: name });
    }
  });
  const running: RunningSidecar = {
    process: { pid: 42, exited: exit.promise, signal, stop: vi.fn() },
    transport: { baseUrl: "http://127.0.0.1:43123", credential: "secret" },
    readiness: {
      status: "ready",
      app_version: "0.1.0",
      protocol_version: 1,
      instance_id: "instance-123",
      capabilities: [],
    },
  };
  const dependencies: SidecarShutdownDependencies = {
    requestDrain: vi.fn().mockResolvedValue({ status: "closed" }),
    signalResources: vi.fn().mockResolvedValue(undefined),
    countResources: vi.fn().mockResolvedValue(0),
    recordExit: vi.fn().mockResolvedValue(undefined),
    delay: vi.fn().mockResolvedValue(undefined),
    now: vi
      .fn()
      .mockReturnValueOnce(new Date("2026-08-03T01:02:03.000Z"))
      .mockReturnValueOnce(new Date("2026-08-03T01:02:04.250Z")),
  };
  return { running, dependencies, signal };
}

it("uses cooperative drain and TERM for a responsive sidecar", async () => {
  const { running, dependencies, signal } = fixture(false);

  const result = await shutdownSidecar(
    running,
    OPTIONS,
    "app_exit",
    dependencies,
  );

  expect(result).toEqual({
    status: "closed",
    remainingResourceCount: 0,
    forced: false,
    exitMarkerRecorded: true,
  });
  expect(signal).toHaveBeenCalledWith("SIGTERM");
  expect(signal).not.toHaveBeenCalledWith("SIGKILL");
  expect(dependencies.recordExit).toHaveBeenCalledWith(OPTIONS, {
    exitReason: "app_exit",
    requestedAt: "2026-08-03T01:02:03.000Z",
    completedAt: "2026-08-03T01:02:04.250Z",
    exitMode: "cooperative",
    processTreeResult: "closed",
    remainingResourceCount: 0,
  });
});

it("escalates a hung sidecar and snapshot resources to KILL", async () => {
  const { running, dependencies, signal } = fixture(true);
  vi.mocked(dependencies.requestDrain).mockRejectedValue(new Error("timeout"));

  const result = await shutdownSidecar(
    running,
    OPTIONS,
    "app_exit",
    dependencies,
  );

  expect(result.forced).toBe(true);
  expect(signal.mock.calls.map(([name]) => name)).toEqual([
    "SIGTERM",
    "SIGKILL",
  ]);
  expect(dependencies.signalResources).toHaveBeenNthCalledWith(
    1,
    OPTIONS,
    "SIGTERM",
  );
  expect(dependencies.signalResources).toHaveBeenNthCalledWith(
    2,
    OPTIONS,
    "SIGKILL",
  );
});

it("records needs_reconcile when the final snapshot cannot be verified", async () => {
  const { running, dependencies } = fixture(false);
  vi.mocked(dependencies.countResources).mockRejectedValue(
    new Error("resource snapshot unavailable"),
  );

  const result = await shutdownSidecar(
    running,
    OPTIONS,
    "app_exit",
    dependencies,
  );

  expect(result).toEqual({
    status: "needs_reconcile",
    remainingResourceCount: 1,
    forced: true,
    exitMarkerRecorded: true,
  });
  expect(dependencies.recordExit).toHaveBeenCalledWith(OPTIONS, {
    exitReason: "app_exit",
    requestedAt: "2026-08-03T01:02:03.000Z",
    completedAt: "2026-08-03T01:02:04.250Z",
    exitMode: "forced",
    processTreeResult: "needs_reconcile",
    remainingResourceCount: 1,
  });
});

it("records readiness-loss cleanup as an abnormal sidecar exit", async () => {
  const { running, dependencies } = fixture(false);

  await shutdownSidecar(running, OPTIONS, "sidecar_abnormal", dependencies);

  expect(dependencies.recordExit).toHaveBeenCalledWith(
    OPTIONS,
    expect.objectContaining({ exitReason: "sidecar_abnormal" }),
  );
});
