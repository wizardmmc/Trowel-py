/** 配置 macOS Electron 行为 E2E 的串行隔离、超时和去敏产物目录。 */

import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: ".",
  testMatch: ["agent/**/*.spec.mjs", "discussion/**/*.spec.mjs", "lifecycle/**/*.spec.mjs"],
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  outputDir:
    process.env.TROWEL_E2E_PLAYWRIGHT_OUTPUT ??
    "../.quality-runs/e2e/playwright-direct",
  reporter: [["./support/privacy-reporter.mjs"]],
  use: {
    screenshot: "off",
    video: "off",
    trace: "off",
  },
});
