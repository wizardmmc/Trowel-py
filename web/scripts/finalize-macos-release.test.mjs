/** 验证正式 macOS 发布按顺序验收 app，并签名、公证和 staple 最终 DMG。 */

// @vitest-environment node

import { expect, it } from "vitest";
import { buildMacReleaseFinalizationCommands } from "./finalize-macos-release.mjs";

it("builds the bounded Developer ID and notarytool command sequence", () => {
  const commands = buildMacReleaseFinalizationCommands({
    appPath: "/build/Trowel.app",
    dmgPath: "/build/Trowel.dmg",
    identity: "Developer ID Application: Example (TEAM123)",
    keychainProfile: "trowel-notary",
  });

  expect(commands).toEqual([
    {
      command: "xcrun",
      args: ["stapler", "validate", "-v", "/build/Trowel.app"],
    },
    {
      command: "codesign",
      args: [
        "--force",
        "--sign",
        "Developer ID Application: Example (TEAM123)",
        "--timestamp",
        "/build/Trowel.dmg",
      ],
    },
    {
      command: "codesign",
      args: ["--verify", "--strict", "--verbose=2", "/build/Trowel.dmg"],
    },
    {
      command: "xcrun",
      args: [
        "notarytool",
        "submit",
        "/build/Trowel.dmg",
        "--keychain-profile",
        "trowel-notary",
        "--wait",
        "--timeout",
        "20m",
      ],
    },
    {
      command: "xcrun",
      args: ["stapler", "staple", "-v", "/build/Trowel.dmg"],
    },
    {
      command: "xcrun",
      args: ["stapler", "validate", "-v", "/build/Trowel.dmg"],
    },
  ]);
});
