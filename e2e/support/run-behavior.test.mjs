/** 验证 Playwright 退出后的内部状态清理。 */

import { afterEach, describe, expect, test } from "bun:test";
import { access, mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { removeLastRunState } from "./run-behavior.mjs";

const temporaryRoots = [];

afterEach(async () => {
  await Promise.all(
    temporaryRoots.splice(0).map((root) => rm(root, { recursive: true, force: true })),
  );
});

describe("behavior runner state", () => {
  test("removes Playwright last-run state after the child process has exited", async () => {
    const root = await mkdtemp(path.join(os.tmpdir(), "trowel-runner-state-"));
    temporaryRoots.push(root);
    await mkdir(root, { recursive: true });
    const statePath = path.join(root, ".last-run.json");
    await writeFile(statePath, "{}", "utf8");

    await removeLastRunState(root);

    await expect(access(statePath)).rejects.toMatchObject({ code: "ENOENT" });
  });
});
