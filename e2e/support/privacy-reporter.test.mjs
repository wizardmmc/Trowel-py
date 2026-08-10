/** 验证 reporter 能在 Playwright 内部收尾之后删除原生产物并使运行失败。 */

import { afterEach, describe, expect, test } from "bun:test";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import PrivacyReporter from "./privacy-reporter.mjs";

const temporaryRoots = [];

afterEach(async () => {
  await Promise.all(
    temporaryRoots.splice(0).map((root) => rm(root, { recursive: true, force: true })),
  );
});

describe("privacy reporter", () => {
  test("deletes an automatic error context and overrides the final status", async () => {
    const root = await mkdtemp(path.join(os.tmpdir(), "trowel-reporter-test-"));
    temporaryRoots.push(root);
    const errorContext = path.join(root, "error-context.md");
    const lastRun = path.join(root, ".last-run.json");
    await writeFile(errorContext, "private renderer state", "utf8");
    await writeFile(lastRun, "{}\n", "utf8");
    const output = [];
    const reporter = new PrivacyReporter({ write: (message) => output.push(message) });
    reporter.onBegin({ outputDir: root }, { allTests: () => [{ title: "scenario" }] });

    reporter.onTestEnd({ title: "scenario" }, {
      status: "failed",
      errors: [{ message: "TimeoutError: locator.click exceeded" }],
      attachments: [{ name: "error-context", contentType: "text/markdown", path: errorContext }],
    });

    await expect(readFile(errorContext, "utf8")).rejects.toMatchObject({ code: "ENOENT" });
    expect(reporter.onEnd({ status: "failed" })).toEqual({ status: "failed" });
    await expect(readFile(lastRun, "utf8")).rejects.toMatchObject({ code: "ENOENT" });
    await writeFile(lastRun, "{}\n", "utf8");
    reporter.onExit();
    await expect(readFile(lastRun, "utf8")).rejects.toMatchObject({ code: "ENOENT" });
    expect(output.join("")).toBe(
      "Running 1 sanitized behavior test(s)\n" +
        "[1/1] FAILED scenario\n" +
        "failure_category=locator_click_timeout\n" +
        "Behavior run failed; privacy violations: 1\n",
    );
  });

  test("keeps a clean run status unchanged", () => {
    const reporter = new PrivacyReporter({ write: () => {} });
    reporter.onBegin({}, { allTests: () => [{ title: "scenario" }] });
    reporter.onTestEnd(
      { title: "scenario" },
      { status: "passed", errors: [], attachments: [] },
    );
    expect(reporter.onEnd({ status: "passed" })).toBeUndefined();
  });
});
