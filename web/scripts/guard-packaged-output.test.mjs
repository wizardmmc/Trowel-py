/** 验证打包前置门只拦截来自待覆盖 App bundle 的进程。 */

// @vitest-environment node

import { describe, expect, it } from "vitest";
import {
  findPackagedOutputProcesses,
  packagedAppPath,
} from "./guard-packaged-output.mjs";

describe("packaged output guard", () => {
  it("finds the app and helper processes running from the output bundle", () => {
    const appPath = "/repo/web/out/Trowel-darwin-arm64/Trowel.app";
    const processTable = `
 101 /repo/web/out/Trowel-darwin-arm64/Trowel.app/Contents/MacOS/Trowel
 102 /repo/web/out/Trowel-darwin-arm64/Trowel.app/Contents/Frameworks/Trowel Helper.app/Contents/MacOS/Trowel Helper --type=renderer
 103 /Applications/Trowel.app/Contents/MacOS/Trowel
`;

    expect(findPackagedOutputProcesses(processTable, appPath)).toEqual([
      {
        pid: 101,
        command:
          "/repo/web/out/Trowel-darwin-arm64/Trowel.app/Contents/MacOS/Trowel",
      },
      {
        pid: 102,
        command:
          "/repo/web/out/Trowel-darwin-arm64/Trowel.app/Contents/Frameworks/Trowel Helper.app/Contents/MacOS/Trowel Helper --type=renderer",
      },
    ]);
  });

  it("builds the exact Forge output path for the selected target", () => {
    expect(packagedAppPath("/repo/web", "darwin", "arm64")).toBe(
      "/repo/web/out/Trowel-darwin-arm64/Trowel.app",
    );
  });
});
