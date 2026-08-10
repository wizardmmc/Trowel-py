/** 在 Playwright 完成内部收尾后删除未经审计的原生产物。 */

import { rmSync } from "node:fs";
import { classifyFailureErrors } from "./artifacts.mjs";

/**
 * Playwright 的 error-context 在测试 fixture teardown 之后才生成，必须由 reporter
 * 在 onTestEnd 阶段兜底删除；发现任何原生 attachment 都让整次运行失败。
 */
export default class PrivacyReporter {
  constructor(options = {}) {
    this.violationCount = 0;
    this.completedCount = 0;
    this.totalCount = 0;
    this.write = options.write ?? ((message) => globalThis.process.stdout.write(message));
    this.outputDirectory = null;
  }

  /** 只输出场景总数，不打印配置、项目路径或环境。 */
  onBegin(config, suite) {
    this.outputDirectory = config.outputDir ?? null;
    this.totalCount = suite.allTests().length;
    this.write(`Running ${this.totalCount} sanitized behavior test(s)\n`);
  }

  /** 删除原生附件，并且只打印静态场景标题和状态。 */
  onTestEnd(test, result) {
    this.completedCount += 1;
    this.write(
      `[${this.completedCount}/${this.totalCount}] ${result.status.toUpperCase()} ${test.title}\n`,
    );
    if (result.status !== "passed") {
      this.write(`failure_category=${classifyFailureErrors(result.errors).join(",")}\n`);
    }
    if (result.attachments.length > 0) {
      this.violationCount += result.attachments.length;
      for (const attachment of result.attachments) {
        if (!attachment.path) continue;
        rmSync(attachment.path, { recursive: true, force: true });
      }
    }
  }

  /** 产物审计命中时覆盖最终状态，防止“测试绿但泄漏产物”的假成功。 */
  onEnd(result) {
    this.removeRunnerState();
    const status = this.violationCount > 0 ? "failed" : result.status;
    this.write(`Behavior run ${status}; privacy violations: ${this.violationCount}\n`);
    return this.violationCount > 0 ? { status: "failed" } : undefined;
  }

  /** Playwright 在 onEnd 后写入 runner 状态，因此退出前再清理一次。 */
  onExit() {
    this.removeRunnerState();
  }

  /** 删除只服务于 Playwright 自身重跑、无需上传的哈希测试 ID 文件。 */
  removeRunnerState() {
    if (!this.outputDirectory) return;
    rmSync(`${this.outputDirectory}/.last-run.json`, { force: true });
  }
}
