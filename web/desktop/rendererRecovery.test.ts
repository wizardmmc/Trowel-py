/** 验证 renderer 异常退出后的有界恢复决策。 */

// @vitest-environment node

import { expect, it } from "vitest";
import { decideRendererRecovery } from "./rendererRecovery";

it("reloads once after a renderer crash", () => {
  expect(
    decideRendererRecovery({
      crashedPage: "renderer",
      finalQuit: false,
      reason: "crashed",
      previousCrashAt: null,
      now: 10_000,
    }),
  ).toBe("reload");
});

it("opens diagnostics when the recovered renderer crashes again soon", () => {
  expect(
    decideRendererRecovery({
      crashedPage: "renderer",
      finalQuit: false,
      reason: "oom",
      previousCrashAt: 10_000,
      now: 20_000,
    }),
  ).toBe("diagnostics");
});

it("does not recover a clean exit, diagnostics page, or final quit", () => {
  expect(
    decideRendererRecovery({
      crashedPage: "renderer",
      finalQuit: false,
      reason: "clean-exit",
      previousCrashAt: null,
      now: 10_000,
    }),
  ).toBe("none");
  expect(
    decideRendererRecovery({
      crashedPage: "diagnostics",
      finalQuit: false,
      reason: "crashed",
      previousCrashAt: null,
      now: 10_000,
    }),
  ).toBe("none");
  expect(
    decideRendererRecovery({
      crashedPage: "renderer",
      finalQuit: true,
      reason: "crashed",
      previousCrashAt: null,
      now: 10_000,
    }),
  ).toBe("none");
});

it("allows another automatic recovery after the crash window expires", () => {
  expect(
    decideRendererRecovery({
      crashedPage: "renderer",
      finalQuit: false,
      reason: "crashed",
      previousCrashAt: 10_000,
      now: 70_001,
    }),
  ).toBe("reload");
});
