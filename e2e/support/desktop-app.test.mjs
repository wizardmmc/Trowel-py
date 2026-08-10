/** 验证 Electron 优雅退出失败后仍执行有界强制清理。 */

import { EventEmitter } from "node:events";
import { describe, expect, test } from "bun:test";
import { DesktopApplication } from "./desktop-app.mjs";

describe("desktop application teardown", () => {
  test("kills and waits for Electron when app.quit cannot be requested", async () => {
    const child = new FakeElectronProcess();
    const application = new DesktopApplication({
      electronApp: {
        process: () => child,
        evaluate: async () => { throw new Error("main process unavailable"); },
      },
      page: {},
      environment: {
        verifyCleanExit: async () => { throw new Error("must not verify a forced exit"); },
      },
      api: {},
      trace: { record: () => {} },
    });

    await expect(application.quit()).rejects.toThrow("main process unavailable");
    expect(child.signals).toEqual(["SIGKILL"]);
    expect(application.exited).toBe(true);
  });

  test("forces Electron down when the main-process quit request never returns", async () => {
    const child = new FakeElectronProcess();
    const application = new DesktopApplication({
      electronApp: {
        process: () => child,
        evaluate: () => new Promise(() => {}),
      },
      page: {},
      environment: { verifyCleanExit: async () => ({}) },
      api: {},
      trace: { record: () => {} },
      quitTimeoutMs: 1,
    });

    await expect(application.quit()).rejects.toThrow("quit request did not finish");
    expect(child.signals).toEqual(["SIGKILL"]);
    expect(application.exited).toBe(true);
  });
});

/** 模拟收到 SIGKILL 后异步发布 exit 的 Electron 子进程。 */
class FakeElectronProcess extends EventEmitter {
  constructor() {
    super();
    this.exitCode = null;
    this.signalCode = null;
    this.signals = [];
  }

  kill(signal) {
    this.signals.push(signal);
    queueMicrotask(() => {
      this.signalCode = signal;
      this.emit("exit", null, signal);
    });
    return true;
  }
}
