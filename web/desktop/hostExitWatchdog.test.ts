/** 验证 macOS Host 退出 watchdog 的平台边界和进程身份参数。 */

// @vitest-environment node

import { EventEmitter } from "node:events";
import { expect, it, vi } from "vitest";
import {
  armHostExitWatchdog,
  type SpawnWatchdog,
} from "./hostExitWatchdog";

function watchdogFixture() {
  const child = new EventEmitter() as EventEmitter & { unref: () => void };
  child.unref = vi.fn();
  const spawnWatchdog = vi.fn(() => child) as unknown as SpawnWatchdog;
  return { child, spawnWatchdog };
}

it("does not arm a watchdog outside macOS", async () => {
  const { spawnWatchdog } = watchdogFixture();

  const armed = await armHostExitWatchdog({
    platform: "win32",
    spawnWatchdog,
  });

  expect(armed).toBe(false);
  expect(spawnWatchdog).not.toHaveBeenCalled();
});

it("arms a detached macOS child with the current Host identity", async () => {
  const { child, spawnWatchdog } = watchdogFixture();
  const arming = armHostExitWatchdog({
    platform: "darwin",
    parentPid: 4321,
    delaySeconds: 2,
    spawnWatchdog,
  });
  child.emit("spawn");

  await expect(arming).resolves.toBe(true);
  expect(spawnWatchdog).toHaveBeenCalledWith(
    "/bin/sh",
    expect.arrayContaining(["trowel-exit-watchdog", "4321", "2"]),
    { detached: true, stdio: "ignore" },
  );
  expect(child.unref).toHaveBeenCalledOnce();
});

it("surfaces a watchdog spawn failure before Electron exits", async () => {
  const { child, spawnWatchdog } = watchdogFixture();
  const arming = armHostExitWatchdog({
    platform: "darwin",
    spawnWatchdog,
  });
  child.emit("error", new Error("spawn failed"));

  await expect(arming).rejects.toThrow("spawn failed");
  expect(child.unref).not.toHaveBeenCalled();
});
