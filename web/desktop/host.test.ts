/** 验证 Desktop Host 只持有 sidecar 启动状态并正确切换产品页和诊断页。 */

// @vitest-environment node

import { expect, it, vi } from "vitest";
import { DesktopHost } from "./host";
import { SidecarStartError, type RunningSidecar } from "./sidecar";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

function runningSidecar(exit = deferred<{ code: number | null; signal: string | null }>()) {
  const running: RunningSidecar = {
    process: { pid: 42, exited: exit.promise, signal: vi.fn(), stop: vi.fn() },
    transport: {
      baseUrl: "http://127.0.0.1:43123",
      credential: "desktop-secret",
    },
    readiness: {
      status: "ready",
      app_version: "0.1.0",
      protocol_version: 1,
      instance_id: "instance-123",
      capabilities: ["agent", "memory", "review"],
    },
  };
  return { running, exit };
}

const OPTIONS = {
  executable: "/repo/.venv/bin/python",
  cwd: "/repo",
  dataDirectory: "/data",
  logDirectory: "/logs",
  instanceId: "instance-123",
  credential: "desktop-secret",
  expectedAppVersion: "0.1.0",
  rendererOrigin: "http://127.0.0.1:43124",
};

it("loads the renderer only after the sidecar is ready", async () => {
  const { running } = runningSidecar();
  const loadRenderer = vi.fn();
  const host = new DesktopHost(OPTIONS, {
    launch: vi.fn().mockResolvedValue(running),
    loadRenderer,
    loadDiagnostics: vi.fn(),
  });

  await host.start();

  expect(loadRenderer).toHaveBeenCalledOnce();
  expect(host.context()).toEqual({
    environment: "desktop",
    appVersion: "0.1.0",
    instanceId: "instance-123",
    transport: running.transport,
  });
  expect(host.diagnostics().status).toBe("ready");
});

it("shows diagnostics for startup failure and can retry", async () => {
  const { running } = runningSidecar();
  const launch = vi
    .fn()
    .mockRejectedValueOnce(
      new SidecarStartError("readiness_timeout", "sidecar timed out"),
    )
    .mockResolvedValueOnce(running);
  const loadRenderer = vi.fn();
  const loadDiagnostics = vi.fn();
  const host = new DesktopHost(OPTIONS, {
    launch,
    loadRenderer,
    loadDiagnostics,
  });

  await host.start();
  expect(host.diagnostics()).toMatchObject({
    status: "failed",
    category: "readiness_timeout",
  });
  expect(loadDiagnostics).toHaveBeenCalledOnce();

  await host.retry();
  expect(loadRenderer).toHaveBeenCalledOnce();
  expect(host.diagnostics().status).toBe("ready");
});

it("moves a ready window to diagnostics if its current sidecar exits", async () => {
  const { running, exit } = runningSidecar();
  const loadDiagnostics = vi.fn();
  const host = new DesktopHost(OPTIONS, {
    launch: vi.fn().mockResolvedValue(running),
    loadRenderer: vi.fn(),
    loadDiagnostics,
  });
  await host.start();

  exit.resolve({ code: 9, signal: null });
  await vi.waitFor(() => expect(loadDiagnostics).toHaveBeenCalledOnce());

  expect(host.diagnostics()).toMatchObject({
    status: "failed",
    category: "early_exit",
    exitCode: 9,
  });
});

it("keeps diagnostics in front when the sidecar exits during renderer loading", async () => {
  const { running, exit } = runningSidecar();
  const rendererGate = deferred<void>();
  const loadOrder: string[] = [];
  const host = new DesktopHost(OPTIONS, {
    launch: vi.fn().mockResolvedValue(running),
    loadRenderer: vi.fn(async () => {
      await rendererGate.promise;
      loadOrder.push("renderer");
    }),
    loadDiagnostics: vi.fn(() => {
      loadOrder.push("diagnostics");
    }),
  });
  const starting = host.start();
  await vi.waitFor(() => expect(host.diagnostics().status).toBe("ready"));

  exit.resolve({ code: 9, signal: null });
  await Promise.resolve();
  rendererGate.resolve();
  await starting;
  await vi.waitFor(() => expect(host.diagnostics().status).toBe("failed"));

  expect(loadOrder.at(-1)).toBe("diagnostics");
});

it("stops the sidecar if loading the renderer fails", async () => {
  const { running } = runningSidecar();
  const shutdown = vi.fn().mockResolvedValue({
    status: "closed",
    remainingResourceCount: 0,
    forced: false,
  });
  const host = new DesktopHost(OPTIONS, {
    launch: vi.fn().mockResolvedValue(running),
    shutdown,
    loadRenderer: vi.fn().mockRejectedValue(new Error("renderer failed")),
    loadDiagnostics: vi.fn(),
  });

  await host.start();

  expect(shutdown).toHaveBeenCalledWith(running, OPTIONS);
  expect(host.diagnostics()).toMatchObject({
    status: "failed",
    category: "early_exit",
  });
});

it("waits for an in-progress launch and shuts down the late sidecar", async () => {
  const { running } = runningSidecar();
  const launch = deferred<RunningSidecar>();
  const shutdown = vi.fn().mockResolvedValue({
    status: "closed",
    remainingResourceCount: 0,
    forced: false,
  });
  const host = new DesktopHost(OPTIONS, {
    launch: vi.fn(() => launch.promise),
    shutdown,
    loadRenderer: vi.fn(),
    loadDiagnostics: vi.fn(),
  });

  const starting = host.start();
  const stopping = host.stop();
  let stopped = false;
  void stopping.then(() => {
    stopped = true;
  });
  await Promise.resolve();
  expect(stopped).toBe(false);

  launch.resolve(running);
  await Promise.all([starting, stopping]);

  expect(shutdown).toHaveBeenCalledWith(running, OPTIONS);
  expect(running.process.stop).not.toHaveBeenCalled();
});

it("reuses one asynchronous shutdown for repeated stop calls", async () => {
  const { running } = runningSidecar();
  const shutdown = vi.fn().mockResolvedValue({
    status: "closed",
    remainingResourceCount: 0,
    forced: false,
  });
  const host = new DesktopHost(OPTIONS, {
    launch: vi.fn().mockResolvedValue(running),
    shutdown,
    loadRenderer: vi.fn(),
    loadDiagnostics: vi.fn(),
  });
  await host.start();

  const [first, second] = await Promise.all([host.stop(), host.stop()]);

  expect(first).toBe(second);
  expect(shutdown).toHaveBeenCalledOnce();
});
