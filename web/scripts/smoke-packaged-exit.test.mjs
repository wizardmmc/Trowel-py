/** 验证整包 smoke 接受正常退出和完成清理后的 watchdog 终态。 */

// @vitest-environment node

import { expect, it } from "vitest";
import { isAcceptedPackagedAppExit } from "./smoke-packaged-exit.mjs";

it("accepts a normal zero exit", () => {
  expect(isAcceptedPackagedAppExit({ code: 0, signal: null })).toBe(true);
});

it("accepts the macOS exit watchdog SIGKILL", () => {
  expect(isAcceptedPackagedAppExit({ code: null, signal: "SIGKILL" })).toBe(true);
});

it.each([
  { code: 1, signal: null },
  { code: null, signal: "SIGTERM" },
  { code: null, signal: null },
])("rejects an unrelated process failure: %o", (result) => {
  expect(isAcceptedPackagedAppExit(result)).toBe(false);
});
