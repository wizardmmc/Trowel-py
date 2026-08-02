/** 验证 Electron Host 不会把根 PID 消失误判为整个进程组已经退出。 */

// @vitest-environment node

import { expect, it, vi } from "vitest";
import { countLiveSnapshotResources } from "./resourceCleanup";

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

it("counts a live process group after its recorded root PID exits", async () => {
  const dependencies = {
    readSnapshot: vi.fn().mockResolvedValue({
      version: 1,
      app_instance_id: "instance-hash",
      resources: [
        {
          resource_kind: "codex_app_server_process_group",
          state: "needs_reconcile",
          pid: 410,
          process_group: 410,
          process_start_identity: "start-410",
        },
      ],
    }),
    inspectProcess: vi.fn().mockResolvedValue(null),
    processGroupAlive: vi.fn().mockReturnValue(true),
  };

  const live = await countLiveSnapshotResources(OPTIONS, dependencies);

  expect(live).toBe(1);
});

it("rejects an unavailable snapshot instead of reporting zero", async () => {
  const dependencies = {
    readSnapshot: vi.fn().mockResolvedValue(null),
    inspectProcess: vi.fn(),
    processGroupAlive: vi.fn(),
  };

  await expect(
    countLiveSnapshotResources(OPTIONS, dependencies),
  ).rejects.toThrow(/snapshot unavailable/);
});
