/** 验证 Electron Host 启动 Python sidecar 时的握手和错误分类。 */

// @vitest-environment node

import { describe, expect, it, vi } from "vitest";
import {
  SidecarStartError,
  launchSidecar,
  type SidecarProcess,
  type SidecarReadiness,
  type SidecarStartDependencies,
} from "./sidecar";

const READY: SidecarReadiness = {
  status: "ready",
  app_version: "0.1.0",
  protocol_version: 1,
  instance_id: "instance-123",
  capabilities: ["agent", "memory", "review"],
};

function pendingExit(): Promise<{ code: number | null; signal: string | null }> {
  return new Promise(() => undefined);
}

function processHandle(
  exited: Promise<{ code: number | null; signal: string | null }> = pendingExit(),
): SidecarProcess {
  return { pid: 321, exited, signal: vi.fn(), stop: vi.fn() };
}

function dependencies(
  overrides: Partial<SidecarStartDependencies> = {},
): SidecarStartDependencies {
  return {
    reservePort: vi.fn().mockResolvedValue(43123),
    spawn: vi.fn().mockReturnValue(processHandle()),
    readReadiness: vi.fn().mockResolvedValue(READY),
    delay: vi.fn().mockResolvedValue(undefined),
    cleanup: vi.fn().mockResolvedValue(undefined),
    ...overrides,
  };
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
  rendererOrigin: "http://127.0.0.1:43124",
  readinessTimeoutMs: 20,
  pollIntervalMs: 1,
};

describe("launchSidecar", () => {
  it("passes credentials through the environment and validates readiness", async () => {
    const deps = dependencies();

    const running = await launchSidecar(OPTIONS, deps);

    expect(running.transport).toEqual({
      baseUrl: "http://127.0.0.1:43123",
      credential: "desktop-secret",
    });
    expect(deps.spawn).toHaveBeenCalledWith(
      expect.objectContaining({
        executable: "/repo/.venv/bin/python",
        args: ["-m", "trowel_py.desktop.sidecar"],
        environment: expect.objectContaining({
          TROWEL_DESKTOP_CREDENTIAL: "desktop-secret",
          TROWEL_APP_INSTANCE_ID: "instance-123",
          TROWEL_SERVER_PORT: "43123",
          TROWEL_DESKTOP_DATA_MODE: "packaged",
          TROWEL_DESKTOP_RENDERER_ORIGIN: "http://127.0.0.1:43124",
        }),
      }),
    );
    expect(JSON.stringify(vi.mocked(deps.spawn).mock.calls[0]?.[0].args)).not.toContain(
      "desktop-secret",
    );
  });

  it("starts a frozen packaged sidecar without Python module arguments", async () => {
    const deps = dependencies();

    await launchSidecar(
      {
        ...OPTIONS,
        command: {
          executable: "/Applications/Trowel.app/Contents/Resources/sidecar/trowel-sidecar",
          args: [],
        },
      },
      deps,
    );

    expect(deps.spawn).toHaveBeenCalledWith(
      expect.objectContaining({
        executable:
          "/Applications/Trowel.app/Contents/Resources/sidecar/trowel-sidecar",
        args: [],
      }),
    );
  });

  it("rejects a sidecar from another version", async () => {
    const proc = processHandle();
    const deps = dependencies({
      spawn: vi.fn().mockReturnValue(proc),
      readReadiness: vi.fn().mockResolvedValue({
        ...READY,
        app_version: "0.2.0",
      }),
    });

    await expect(launchSidecar(OPTIONS, deps)).rejects.toMatchObject({
      category: "version_mismatch",
    });
    expect(deps.cleanup).toHaveBeenCalledWith(
      {
        process: proc,
        transport: {
          baseUrl: "http://127.0.0.1:43123",
          credential: "desktop-secret",
        },
      },
      OPTIONS,
    );
    expect(proc.stop).not.toHaveBeenCalled();
  });

  it("classifies an early process exit", async () => {
    const deps = dependencies({
      spawn: vi.fn().mockReturnValue(
        processHandle(Promise.resolve({ code: 17, signal: null })),
      ),
      readReadiness: vi.fn().mockRejectedValue(new Error("not ready")),
    });

    await expect(launchSidecar(OPTIONS, deps)).rejects.toMatchObject({
      category: "early_exit",
      exitCode: 17,
    });
  });

  it.each([
    ["ENOENT", "executable_missing"],
    ["EACCES", "port_or_permission"],
    ["EADDRINUSE", "port_or_permission"],
  ] as const)("maps spawn error %s to %s", async (code, category) => {
    const error = Object.assign(new Error(code), { code });
    const deps = dependencies({ spawn: vi.fn(() => { throw error; }) });

    await expect(launchSidecar(OPTIONS, deps)).rejects.toBeInstanceOf(
      SidecarStartError,
    );
    await expect(launchSidecar(OPTIONS, deps)).rejects.toMatchObject({ category });
  });

  it("times out when readiness never succeeds", async () => {
    const deps = dependencies({
      readReadiness: vi.fn().mockRejectedValue(new Error("not ready")),
      delay: () => new Promise((resolve) => setTimeout(resolve, 2)),
    });

    await expect(launchSidecar(OPTIONS, deps)).rejects.toMatchObject({
      category: "readiness_timeout",
    });
  });

  it("classifies a loopback port reservation failure", async () => {
    const deps = dependencies({
      reservePort: vi.fn().mockRejectedValue(
        Object.assign(new Error("permission denied"), { code: "EACCES" }),
      ),
    });

    await expect(launchSidecar(OPTIONS, deps)).rejects.toMatchObject({
      category: "port_or_permission",
    });
  });
});
