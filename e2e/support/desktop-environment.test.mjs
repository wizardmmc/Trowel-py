/** 验证隔离环境在局部清理失败时仍会继续回收其余资源。 */

import { EventEmitter } from "node:events";
import { afterEach, describe, expect, test } from "bun:test";
import { access, mkdir, mkdtemp, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import {
  DesktopTestEnvironment,
  rollbackDesktopEnvironmentCreation,
  terminateChildProcess,
} from "./desktop-environment.mjs";

const temporaryRoots = [];

afterEach(async () => {
  await Promise.all(
    temporaryRoots.splice(0).map((root) => rm(root, { recursive: true, force: true })),
  );
});

describe("desktop environment teardown", () => {
  test("waits for SIGKILL exit after the graceful Vite deadline", async () => {
    const child = new FakeChild();

    await terminateChildProcess(child, { graceMs: 0, finalMs: 100 });

    expect(child.signals).toEqual(["SIGTERM", "SIGKILL"]);
    expect(child.signalCode).toBe("SIGKILL");
  });

  test("removes the isolated root even when model catalog close fails", async () => {
    const root = await mkdtemp(path.join(os.tmpdir(), "trowel-environment-test-"));
    temporaryRoots.push(root);
    await mkdir(path.join(root, "data"), { recursive: true });
    const environment = new DesktopTestEnvironment({
      root,
      rendererUrl: "http://127.0.0.1:1",
      electronEnv: {},
      vite: { exitCode: 0, signalCode: null },
      modelCatalog: { close: async () => { throw new Error("catalog close failed"); } },
    });

    await expect(environment.dispose()).rejects.toThrow("catalog close failed");
    await expect(access(root)).rejects.toMatchObject({ code: "ENOENT" });
  });

  test("reports setup and cleanup failures after still removing the root", async () => {
    const root = await mkdtemp(path.join(os.tmpdir(), "trowel-rollback-test-"));
    temporaryRoots.push(root);
    const vite = {
      exitCode: null,
      signalCode: null,
      kill: () => { throw new Error("vite termination failed"); },
    };

    const rollback = rollbackDesktopEnvironmentCreation({
      root,
      vite,
      modelCatalog: {
        close: async () => { throw new Error("catalog rollback failed"); },
      },
      setupError: new Error("setup failed"),
    });

    await expect(rollback).rejects.toMatchObject({
      errors: [
        { message: "setup failed" },
        { message: "vite termination failed" },
        { message: "catalog rollback failed" },
      ],
    });
    await expect(access(root)).rejects.toMatchObject({ code: "ENOENT" });
  });
});

/** 模拟只在 SIGKILL 后真正退出的子进程。 */
class FakeChild extends EventEmitter {
  constructor() {
    super();
    this.exitCode = null;
    this.signalCode = null;
    this.signals = [];
  }

  kill(signal) {
    this.signals.push(signal);
    if (signal === "SIGKILL") {
      queueMicrotask(() => {
        this.signalCode = signal;
        this.emit("exit", null, signal);
      });
    }
    return true;
  }
}
