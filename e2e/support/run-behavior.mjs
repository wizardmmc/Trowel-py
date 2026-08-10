/** 启动 Playwright 行为测试，并在 runner 真正退出后清理内部状态文件。 */

import { spawn } from "node:child_process";
import { rm } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  clearBehaviorEvidence,
  createPlaywrightScratch,
  publishSanitizedEvidence,
  writeRepeatEvidenceManifest,
} from "./evidence.mjs";

const supportDirectory = path.dirname(fileURLToPath(import.meta.url));
const e2eRoot = path.resolve(supportDirectory, "..");
/** 删除 Playwright 为 `--last-failed` 保存、但不允许上传的内部测试 ID。 */
export async function removeLastRunState(directory) {
  await rm(path.join(directory, ".last-run.json"), { force: true });
}

/** 以继承终端的子进程运行 Playwright，并原样返回退出码。 */
export async function runBehavior(args = process.argv.slice(2)) {
  const cleanEvidence = args.includes("--clean-evidence");
  const playwrightArgs = args.filter((argument) => argument !== "--clean-evidence");
  const repeatEach = readRepeatEach(playwrightArgs);
  if (cleanEvidence) await clearBehaviorEvidence();
  const scratchDirectory = await createPlaywrightScratch();
  const executable = path.join(e2eRoot, "node_modules", ".bin", "playwright");
  const child = spawn(executable, ["test", ...playwrightArgs], {
    cwd: e2eRoot,
    stdio: "inherit",
    env: {
      ...process.env,
      TROWEL_E2E_PLAYWRIGHT_OUTPUT: scratchDirectory,
    },
  });
  const signals = ["SIGINT", "SIGTERM"];
  const forwarders = new Map(
    signals.map((signal) => [signal, () => child.kill(signal)]),
  );
  for (const [signal, forward] of forwarders) process.once(signal, forward);
  let runExitCode = 1;
  try {
    const result = await new Promise((resolve) => {
      child.once("error", (error) => resolve({ code: 1, error, signal: null }));
      child.once("exit", (code, signal) => resolve({ code, error: null, signal }));
    });
    if (result.error) throw result.error;
    runExitCode = result.code ?? signalExitCode(result.signal);
    return runExitCode;
  } finally {
    for (const [signal, forward] of forwarders) {
      process.off(signal, forward);
    }
    await removeLastRunState(scratchDirectory);
    try {
      await publishSanitizedEvidence(scratchDirectory);
      if (repeatEach !== null) {
        const manifest = await writeRepeatEvidenceManifest(
          {
            repeatEach,
            exitCode: runExitCode,
            retryCount: 0,
            privacyViolationCount: runExitCode === 0 ? 0 : null,
          },
          { evidenceDirectory: scratchDirectory },
        );
        process.stdout.write(`Repeat evidence: ${path.basename(path.dirname(manifest))}\n`);
      }
    } finally {
      await rm(scratchDirectory, { recursive: true, force: true });
    }
  }
}

/** 从 Playwright 参数读取大于一次的 repeat-each；普通行为叶子不生成重复清单。 */
function readRepeatEach(args) {
  const inline = args.find((argument) => argument.startsWith("--repeat-each="));
  const flagIndex = args.indexOf("--repeat-each");
  const raw = inline?.slice("--repeat-each=".length)
    ?? (flagIndex >= 0 ? args[flagIndex + 1] : null);
  if (raw === null) return null;
  const parsed = Number(raw);
  return Number.isInteger(parsed) && parsed > 1 ? parsed : null;
}

/** 把常见终止信号映射成 shell 约定的 128+signal 退出码。 */
function signalExitCode(signal) {
  return signal === "SIGINT" ? 130 : signal === "SIGTERM" ? 143 : 1;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  process.exitCode = await runBehavior();
}
