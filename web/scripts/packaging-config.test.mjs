/** 冻结桌面发布包不得携带仓库 config.toml 的打包契约。 */

// @vitest-environment node

import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { expect, it } from "vitest";

const scriptsDirectory = path.dirname(fileURLToPath(import.meta.url));
const webRoot = path.resolve(scriptsDirectory, "..");
const require = createRequire(import.meta.url);
const packageJson = require(path.join(webRoot, "package.json"));

it("excludes config.toml from Electron sources and extra resources", () => {
  const forgeConfig = require(path.join(webRoot, "forge.config.cjs"));
  const extraResources = forgeConfig.packagerConfig.extraResource.map((value) =>
    path.resolve(value),
  );

  expect(forgeConfig.packagerConfig.ignore("/config.toml")).toBe(true);
  expect(extraResources).not.toContain(path.resolve(webRoot, "..", "config.toml"));
});

it("checks the packaged output before work starts and immediately before Forge", () => {
  for (const scriptName of ["package:mac", "make:mac"]) {
    const script = packageJson.scripts[scriptName];
    const guard = "node scripts/guard-packaged-output.mjs";

    expect(script.split(guard)).toHaveLength(3);
    expect(script).toContain(`${guard} && electron-forge`);
  }
});
