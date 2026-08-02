/** 验证整包 smoke 与正式 Electron Host 使用同一个业务数据层级。 */

// @vitest-environment node

import { expect, it } from "vitest";
import { resolvePackagedSmokeDataDirectory } from "./smoke-packaged-paths.mjs";

it("checks packaged default business data under Trowel/data", () => {
  expect(
    resolvePackagedSmokeDataDirectory({
      defaultPathsSmoke: true,
      homeDirectory: "/Users/smoke",
      smokeRoot: "/tmp/smoke",
    }),
  ).toBe("/Users/smoke/Library/Application Support/Trowel/data");
});

it("keeps explicitly isolated packaged smoke data under its smoke root", () => {
  expect(
    resolvePackagedSmokeDataDirectory({
      defaultPathsSmoke: false,
      homeDirectory: "/tmp/smoke/home",
      smokeRoot: "/tmp/smoke",
    }),
  ).toBe("/tmp/smoke/data");
});
