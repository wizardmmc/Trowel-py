/** 验证各 Playwright 叶子的临时输出只累计发布去敏摘要。 */

import { afterEach, describe, expect, test } from "bun:test";
import { access, mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import {
  clearBehaviorEvidence,
  publishSanitizedEvidence,
  verifyEvidenceManifest,
  writeRepeatEvidenceManifest,
} from "./evidence.mjs";

const temporaryRoots = [];

afterEach(async () => {
  await Promise.all(
    temporaryRoots.splice(0).map((root) => rm(root, { recursive: true, force: true })),
  );
});

describe("behavior evidence publication", () => {
  test("accumulates summaries from independent Playwright outputs", async () => {
    const root = await temporaryRoot();
    const destination = path.join(root, "published");
    for (const name of ["first-case", "second-case"]) {
      const scratch = path.join(root, name);
      await mkdir(path.join(scratch, name), { recursive: true });
      await writeFile(
        path.join(scratch, name, "summary.json"),
        '{"schema":"trowel-e2e-trace-v1"}\n',
        "utf8",
      );
      expect(await publishSanitizedEvidence(scratch, destination)).toBe(1);
    }

    expect(await verifyEvidenceManifest(2, destination)).toBe(2);
  });

  test("rejects native Playwright output instead of publishing it", async () => {
    const root = await temporaryRoot();
    const scratch = path.join(root, "scratch");
    await mkdir(scratch, { recursive: true });
    await writeFile(path.join(scratch, "trace.zip"), "raw", "utf8");

    await expect(
      publishSanitizedEvidence(scratch, path.join(root, "published")),
    ).rejects.toMatchObject({ reasons: ["unexpected_playwright_artifact"] });
  });

  test("publishes a sanitized failure summary without treating it as success evidence", async () => {
    const root = await temporaryRoot();
    const scratch = path.join(root, "scratch");
    const destination = path.join(root, "published");
    await mkdir(path.join(scratch, "failed-case"), { recursive: true });
    await writeFile(
      path.join(scratch, "failed-case", "failure-summary.json"),
      '{"schema":"trowel-e2e-failure-v1","console_counts":{"error":1}}\n',
      "utf8",
    );

    expect(await publishSanitizedEvidence(scratch, destination)).toBe(1);
    await expect(verifyEvidenceManifest(1, destination)).rejects.toThrow(
      "expected 1 summaries, found 0",
    );
  });

  test("prepare removes stale accumulated evidence exactly once", async () => {
    const root = await temporaryRoot();
    const evidence = path.join(root, "published");
    await mkdir(evidence, { recursive: true });
    const stale = path.join(evidence, "stale.json");
    await writeFile(stale, "{}", "utf8");

    await clearBehaviorEvidence(evidence);

    await expect(access(stale)).rejects.toMatchObject({ code: "ENOENT" });
  });

  test("repeat manifest survives later behavior evidence preparation", async () => {
    const root = await temporaryRoot();
    const evidence = path.join(root, "artifacts");
    const repeatRuns = path.join(root, "repeat-runs");
    for (const name of ["first-case", "second-case"]) {
      await mkdir(path.join(evidence, name), { recursive: true });
      await writeFile(
        path.join(evidence, name, "summary.json"),
        '{"schema":"trowel-e2e-trace-v1"}\n',
        "utf8",
      );
    }

    const manifest = await writeRepeatEvidenceManifest(
      { repeatEach: 20, exitCode: 0, retryCount: 0, privacyViolationCount: 0 },
      { evidenceDirectory: evidence, repeatRunsDirectory: repeatRuns, runId: "repeat-test" },
    );
    await clearBehaviorEvidence(evidence);

    const parsed = JSON.parse(await readFile(manifest, "utf8"));
    expect(parsed).toMatchObject({
      schema: "trowel-e2e-repeat-v1",
      run_id: "repeat-test",
      status: "passed",
      repeat_each: 20,
      retry_count: 0,
      privacy_violation_count: 0,
      summary_count: 2,
      failure_summary_count: 0,
      artifact_privacy_audited: true,
    });
  });
});

/** 创建由 afterEach 统一回收的临时根。 */
async function temporaryRoot() {
  const root = await mkdtemp(path.join(os.tmpdir(), "trowel-evidence-test-"));
  temporaryRoots.push(root);
  return root;
}
