/** 验证 E2E 只终止身份仍匹配且不属于 runner 的进程组。 */

import { describe, expect, test } from "bun:test";
import { terminateVerifiedProcessGroups } from "./process-groups.mjs";

describe("verified process-group termination", () => {
  test("skips reused and runner-owned processes, and deduplicates matching groups", async () => {
    const signals = [];
    const identities = new Map([
      [900, { processGroup: 90, startIdentity: "runner" }],
      [101, { processGroup: 201, startIdentity: "match" }],
      [102, { processGroup: 202, startIdentity: "reused" }],
      [103, { processGroup: 90, startIdentity: "child-in-runner-group" }],
      [104, { processGroup: 201, startIdentity: "same-group" }],
    ]);
    const snapshot = {
      version: 2,
      app_instance_id: "app-hash",
      data_root_identity: "root-hash",
      resources: [
        resource(101, 201, "match"),
        resource(102, 202, "old-identity"),
        resource(103, 90, "child-in-runner-group"),
        resource(104, 201, "same-group"),
        { ...resource(105, 205, "closed"), state: "closed" },
      ],
    };

    const count = await terminateVerifiedProcessGroups(snapshot, {
      expectedAppInstanceIdentity: "app-hash",
      expectedDataRootIdentity: "root-hash",
      initialSignal: "SIGKILL",
      graceMs: 0,
      dependencies: {
        hostPid: 900,
        inspectProcess: async (pid) => identities.get(pid) ?? null,
        processGroupAlive: () => false,
        signalGroup: (processGroup, signal) => signals.push([processGroup, signal]),
        delay: async () => {},
      },
    });

    expect(count).toBe(1);
    expect(signals).toEqual([[201, "SIGKILL"]]);
  });

  test("escalates a verified group that survives the grace period", async () => {
    const signals = [];
    let alive = true;
    await terminateVerifiedProcessGroups(
      {
        version: 2,
        app_instance_id: "app-hash",
        data_root_identity: "root-hash",
        resources: [resource(101, 201, "match")],
      },
      {
        expectedAppInstanceIdentity: "app-hash",
        expectedDataRootIdentity: "root-hash",
        graceMs: 0,
        finalMs: 1,
        dependencies: {
          hostPid: 900,
          inspectProcess: async (pid) =>
            pid === 900
              ? { processGroup: 90, startIdentity: "runner" }
              : { processGroup: 201, startIdentity: "match" },
          processGroupAlive: () => alive,
          signalGroup: (processGroup, signal) => {
            signals.push([processGroup, signal]);
            if (signal === "SIGKILL") alive = false;
          },
          delay: async () => {},
        },
      },
    );

    expect(signals).toEqual([
      [201, "SIGTERM"],
      [201, "SIGKILL"],
    ]);
  });

  test("rejects a copied or wrong-instance snapshot before inspecting resources", async () => {
    let inspected = false;
    await expect(
      terminateVerifiedProcessGroups(
        {
          version: 2,
          app_instance_id: "another-app",
          data_root_identity: "root-hash",
          resources: [resource(101, 201, "match")],
        },
        {
          expectedAppInstanceIdentity: "app-hash",
          expectedDataRootIdentity: "root-hash",
          dependencies: {
            hostPid: 900,
            inspectProcess: async () => {
              inspected = true;
              return null;
            },
            processGroupAlive: () => false,
            signalGroup: () => {},
            delay: async () => {},
          },
        },
      ),
    ).rejects.toThrow("untrusted resource snapshot");
    expect(inspected).toBe(false);
  });

  test("refuses all signals when the runner process identity is unavailable", async () => {
    const signals = [];
    await expect(
      terminateVerifiedProcessGroups(
        {
          version: 2,
          app_instance_id: "app-hash",
          data_root_identity: "root-hash",
          resources: [resource(101, 201, "match")],
        },
        {
          expectedAppInstanceIdentity: "app-hash",
          expectedDataRootIdentity: "root-hash",
          dependencies: {
            hostPid: 900,
            inspectProcess: async () => null,
            processGroupAlive: () => false,
            signalGroup: (...args) => signals.push(args),
            delay: async () => {},
          },
        },
      ),
    ).rejects.toThrow("runner identity");
    expect(signals).toEqual([]);
  });
});

/** 构造一条带完整进程身份的活动资源。 */
function resource(pid, processGroup, startIdentity) {
  return {
    state: "active",
    pid,
    process_group: processGroup,
    process_start_identity: startIdentity,
  };
}
