/** 验证 Electron Host 在 sidecar 启动前也能落下生命周期日志。 */

// @vitest-environment node

import { mkdtemp, readFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { expect, it } from "vitest";

import { createLifecycleLogger } from "./lifecycleLogger";

it("creates a missing log directory before writing the first event", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "trowel-lifecycle-log-"));
  try {
    const logDirectory = path.join(root, "missing", "logs");
    const log = createLifecycleLogger(logDirectory);

    log("sidecar_starting");

    const contents = await readFile(
      path.join(logDirectory, "desktop-host.log"),
      "utf8",
    );
    expect(contents).toContain('"event":"sidecar_starting"');
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
