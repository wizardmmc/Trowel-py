/** 管理单次 Playwright 临时输出与跨叶子累计的去敏行为证据。 */

import { mkdir, mkdtemp, readFile, readdir, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { ArtifactPrivacyError, auditArtifactText } from "./artifacts.mjs";

const supportDirectory = path.dirname(fileURLToPath(import.meta.url));
const qualityRoot = path.resolve(supportDirectory, "../../.quality-runs/e2e");

export const behaviorEvidenceDirectory = path.join(qualityRoot, "artifacts");
export const playwrightRunDirectory = path.join(qualityRoot, "playwright-runs");
export const repeatEvidenceDirectory = path.join(qualityRoot, "repeat-runs");

/** 在整个行为 Gate 开始前清空旧证据；各 Playwright 叶子不得自行调用。 */
export async function clearBehaviorEvidence(directory = behaviorEvidenceDirectory) {
  await rm(directory, { recursive: true, force: true });
  await mkdir(directory, { recursive: true });
}

/** 创建不会被其他并发叶子清空的 Playwright 临时 outputDir。 */
export async function createPlaywrightScratch(directory = playwrightRunDirectory) {
  await mkdir(directory, { recursive: true });
  return mkdtemp(path.join(directory, "run-"));
}

/**
 * 把临时 outputDir 中唯一允许保留的摘要重新审计并累计发布。
 *
 * 任何原生附件、runner 状态或符号链接都会让本次运行失败，临时目录随后由调用方删除。
 */
export async function publishSanitizedEvidence(
  scratchDirectory,
  destinationDirectory = behaviorEvidenceDirectory,
) {
  const files = await listFiles(scratchDirectory);
  const allowedNames = new Set(["summary.json", "failure-summary.json"]);
  const unexpected = files.filter((file) => !allowedNames.has(path.basename(file)));
  if (unexpected.length > 0) {
    throw new ArtifactPrivacyError(["unexpected_playwright_artifact"]);
  }
  for (const file of files) {
    const relative = path.relative(scratchDirectory, file);
    const body = await readFile(file, "utf8");
    auditArtifactText(body);
    const destination = path.join(destinationDirectory, relative);
    await mkdir(path.dirname(destination), { recursive: true });
    await writeFile(destination, body, "utf8");
  }
  return files.length;
}

/** 核对聚合 Gate 最终产出的摘要数量，并再次执行隐私审计。 */
export async function verifyEvidenceManifest(
  expectedCount,
  directory = behaviorEvidenceDirectory,
) {
  const files = await listFiles(directory);
  const summaries = files.filter((file) => path.basename(file) === "summary.json");
  if (files.length !== summaries.length || summaries.length !== expectedCount) {
    throw new Error(
      `behavior evidence manifest expected ${expectedCount} summaries, found ${summaries.length}`,
    );
  }
  for (const file of summaries) auditArtifactText(await readFile(file, "utf8"));
  return summaries.length;
}

/**
 * 把一次重复稳定性运行压成不会被后续 behavior prepare 删除的去敏清单。
 *
 * @param {{repeatEach: number, exitCode: number, retryCount: number,
 * privacyViolationCount: number | null}} result 重复次数、退出码、重试数和隐私违规数。
 * @param {{evidenceDirectory?: string, repeatRunsDirectory?: string, runId?: string}} options
 * 当前运行的临时证据目录、持久清单根和可复核运行 ID。
 * @returns {Promise<string>} 已写入的清单路径。
 */
export async function writeRepeatEvidenceManifest(
  result,
  options = {},
) {
  const evidenceDirectory = options.evidenceDirectory ?? behaviorEvidenceDirectory;
  const repeatRunsDirectory = options.repeatRunsDirectory ?? repeatEvidenceDirectory;
  const runId = options.runId ?? createRepeatRunId();
  if (!/^[a-z0-9][a-z0-9._-]{0,127}$/i.test(runId)) {
    throw new Error("repeat evidence run ID is invalid");
  }
  const files = await listFiles(evidenceDirectory);
  for (const file of files) auditArtifactText(await readFile(file, "utf8"));
  const summaryCount = files.filter(
    (file) => path.basename(file) === "summary.json",
  ).length;
  const failureSummaryCount = files.filter(
    (file) => path.basename(file) === "failure-summary.json",
  ).length;
  const passed = result.exitCode === 0 && failureSummaryCount === 0;
  const manifest = {
    schema: "trowel-e2e-repeat-v1",
    run_id: runId,
    status: passed ? "passed" : "failed",
    repeat_each: result.repeatEach,
    exit_code: result.exitCode,
    retry_count: result.retryCount,
    privacy_violation_count: result.privacyViolationCount,
    summary_count: summaryCount,
    failure_summary_count: failureSummaryCount,
    artifact_privacy_audited: true,
  };
  const body = `${JSON.stringify(manifest, null, 2)}\n`;
  auditArtifactText(body);
  const destination = path.join(repeatRunsDirectory, runId, "summary.json");
  await mkdir(path.dirname(destination), { recursive: true });
  await writeFile(destination, body, "utf8");
  return destination;
}

/** 生成只含 UTC 时间和固定前缀的重复运行 ID。 */
function createRepeatRunId(now = new Date()) {
  return `repeat-${now.toISOString().replace(/[-:.]/g, "")}`;
}

/** 递归列出普通文件，并把符号链接等未知目录项视为不允许的产物。 */
async function listFiles(directory) {
  let entries;
  try {
    entries = await readdir(directory, { withFileTypes: true });
  } catch (error) {
    if (error?.code === "ENOENT") return [];
    throw error;
  }
  const files = [];
  for (const entry of entries) {
    const entryPath = path.join(directory, entry.name);
    if (entry.isDirectory()) {
      files.push(...await listFiles(entryPath));
    } else if (entry.isFile()) {
      files.push(entryPath);
    } else {
      throw new ArtifactPrivacyError(["unexpected_playwright_artifact"]);
    }
  }
  return files;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const [command, count] = process.argv.slice(2);
  if (command === "prepare") {
    await clearBehaviorEvidence();
  } else if (command === "verify" && /^\d+$/.test(count ?? "")) {
    await verifyEvidenceManifest(Number(count));
  } else {
    throw new Error("usage: bun support/evidence.mjs prepare|verify <expected-count>");
  }
}
