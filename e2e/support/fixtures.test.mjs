/** 验证桌面 fixture 在 setup 与 teardown 失败时仍保留去敏诊断。 */

import { afterEach, describe, expect, test } from "bun:test";
import { mkdtemp, readFile, readdir, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { removeUnexpectedPlaywrightArtifacts } from "./artifacts.mjs";
import { executeDesktopFixture } from "./fixtures.mjs";

const temporaryRoots = [];

afterEach(async () => {
  await Promise.all(
    temporaryRoots.splice(0).map((root) => rm(root, { recursive: true, force: true })),
  );
});

describe("desktop fixture diagnostics", () => {
  test("writes a failure summary when desktop quit fails after a passing body", async () => {
    const root = await temporaryOutputRoot();
    const page = fakePage();
    let disposed = false;
    const environment = {
      readResourceSnapshot: async () => ({ resources: [] }),
      dispose: async () => {
        disposed = true;
      },
    };
    const desktop = {
      page,
      api: { get: async () => ({ sessions: [] }) },
      exited: false,
      quit: async () => {
        throw new Error("quit failed");
      },
    };

    await expect(
      executeDesktopFixture(async () => {}, fakeTestInfo(root), {
        assertBuildReady: async () => {},
        createEnvironment: async () => environment,
        launchApplication: async () => desktop,
        auditArtifacts: removeUnexpectedPlaywrightArtifacts,
      }),
    ).rejects.toThrow("quit failed");

    expect(disposed).toBe(true);
    expect(await retainedFiles(root)).toEqual([
      "failure-summary.json",
      "summary.json",
    ]);
    expect(JSON.parse(await readFile(path.join(root, "failure-summary.json"), "utf8")))
      .toMatchObject({
        schema: "trowel-e2e-failure-v1",
        failure_categories: ["assertion"],
      });
  });

  test("writes a failure summary when setup fails before creating an environment", async () => {
    const root = await temporaryOutputRoot();
    const privateFailure =
      "build missing at /Users/example/private-project with Bearer private-secret";
    let createCalled = false;

    await expect(
      executeDesktopFixture(async () => {}, fakeTestInfo(root), {
        assertBuildReady: async () => {
          throw new Error(privateFailure);
        },
        createEnvironment: async () => {
          createCalled = true;
          throw new Error("must not run");
        },
        launchApplication: async () => {
          throw new Error("must not run");
        },
        auditArtifacts: removeUnexpectedPlaywrightArtifacts,
      }),
    ).rejects.toThrow("build missing");

    expect(createCalled).toBe(false);
    expect(await retainedFiles(root)).toEqual([
      "failure-summary.json",
      "summary.json",
    ]);
    const failure = JSON.parse(
      await readFile(path.join(root, "failure-summary.json"), "utf8"),
    );
    const retained = JSON.stringify(failure);
    expect(failure.failure_categories).toEqual(["assertion"]);
    expect(retained).not.toContain("/Users/example");
    expect(retained).not.toContain("Bearer");
    expect(retained).not.toContain("private-secret");
    expect(failure.business_state).toEqual({
      dom: { available: false },
      sessions: { available: false },
      resources: { available: false },
      desktop_transport: { available: false },
    });
  });
});

/** 创建由当前测试独占的 Playwright 输出目录。 */
async function temporaryOutputRoot() {
  const root = await mkdtemp(path.join(os.tmpdir(), "trowel-fixture-test-"));
  temporaryRoots.push(root);
  return root;
}

/** 构造 fixture 实际读取的最小 Playwright TestInfo 契约。 */
function fakeTestInfo(outputDir) {
  return {
    title: "fixture diagnostic",
    titlePath: ["support", "fixture diagnostic"],
    outputDir,
    outputPath: (name) => path.join(outputDir, name),
    status: "passed",
    expectedStatus: "passed",
  };
}

/** 构造只暴露白名单 DOM 状态与事件订阅的 renderer 页面。 */
function fakePage() {
  return {
    on: () => {},
    off: () => {},
    evaluate: async () => ({
      available: true,
      turn_status_counts: {},
      dialog_count: 0,
      alert_count: 0,
      disabled_textbox_count: 0,
    }),
  };
}

/** 返回稳定排序后的保留产物名。 */
async function retainedFiles(root) {
  return (await readdir(root)).sort();
}
