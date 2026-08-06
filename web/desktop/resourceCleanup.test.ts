/** 验证 Electron Host 的进程树核验与最终退出标记。 */

// @vitest-environment node

import { createHash } from "node:crypto";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { expect, it, vi } from "vitest";
import { countLiveSnapshotResources, writeExitMarker } from "./resourceCleanup";

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
      version: 2,
      app_instance_id: "instance-hash",
      data_root_identity: "data-root-hash",
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

it("accepts a current v2 empty snapshot as fully reconciled", async () => {
  const dataDirectory = await mkdtemp(
    path.join(os.tmpdir(), "trowel-current-snapshot-"),
  );
  try {
    await writeFile(
      path.join(dataDirectory, "resource-lifecycle.json"),
      JSON.stringify({
        version: 2,
        app_instance_id: redactIdentity(OPTIONS.instanceId),
        data_root_identity: redactIdentity(path.resolve(dataDirectory)),
        resources: [],
      }),
      "utf8",
    );

    await expect(
      countLiveSnapshotResources({ ...OPTIONS, dataDirectory }),
    ).resolves.toBe(0);
  } finally {
    await rm(dataDirectory, { recursive: true, force: true });
  }
});

it("rejects a resource snapshot copied from another data root", async () => {
  const dataDirectory = await mkdtemp(
    path.join(os.tmpdir(), "trowel-foreign-snapshot-"),
  );
  try {
    await writeFile(
      path.join(dataDirectory, "resource-lifecycle.json"),
      JSON.stringify({
        version: 2,
        app_instance_id: redactIdentity(OPTIONS.instanceId),
        data_root_identity: redactIdentity("/original/formal/data-root"),
        resources: [],
      }),
      "utf8",
    );

    await expect(
      countLiveSnapshotResources({ ...OPTIONS, dataDirectory }),
    ).rejects.toThrow(/snapshot unavailable/);
  } finally {
    await rm(dataDirectory, { recursive: true, force: true });
  }
});

it("atomically writes a versioned exit marker without raw instance identity", async () => {
  const dataDirectory = await mkdtemp(
    path.join(os.tmpdir(), "trowel-exit-marker-"),
  );
  try {
    await writeExitMarker(
      { ...OPTIONS, dataDirectory },
      {
        exitReason: "app_exit",
        requestedAt: "2026-08-03T01:02:03.000Z",
        completedAt: "2026-08-03T01:02:04.250Z",
        exitMode: "forced",
        processTreeResult: "needs_reconcile",
        remainingResourceCount: 2,
      },
    );

    const raw = await readFile(
      path.join(dataDirectory, "resource-exit.json"),
      "utf8",
    );
    const marker = JSON.parse(raw);
    expect(marker).toMatchObject({
      version: 2,
      exit_reason: "app_exit",
      requested_at: "2026-08-03T01:02:03.000Z",
      completed_at: "2026-08-03T01:02:04.250Z",
      exit_mode: "forced",
      process_tree_result: "needs_reconcile",
      remaining_resource_count: 2,
    });
    expect(raw).not.toContain(OPTIONS.instanceId);
  } finally {
    await rm(dataDirectory, { recursive: true, force: true });
  }
});

function redactIdentity(value: string): string {
  return createHash("sha256").update(value).digest("hex").slice(0, 20);
}
