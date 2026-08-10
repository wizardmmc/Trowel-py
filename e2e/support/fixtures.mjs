/** 组合隔离环境、真实 Electron、结构化 trace 和强制 teardown。 */

import { test as base } from "@playwright/test";
import {
  FailureDiagnostics,
  StructuredTrace,
  classifyFailureErrors,
  removeUnexpectedPlaywrightArtifacts,
} from "./artifacts.mjs";
import {
  assertDesktopBuildReady,
  createDesktopTestEnvironment,
} from "./desktop-environment.mjs";
import { launchDesktopApplication } from "./desktop-app.mjs";

export const test = base.extend({
  desktop: [async ({}, use, testInfo) => {
    await executeDesktopFixture(use, testInfo);
  }, { timeout: 120_000 }],
});

export { expect } from "@playwright/test";

/** 把测试标题压成临时目录可读且长度有界的标签。 */
function slug(value) {
  return value.replace(/[^a-z0-9]+/gi, "-").replace(/^-|-$/g, "").slice(0, 48) || "case";
}

/**
 * 执行一个完整的桌面测试 fixture，并保证 setup、正文和 teardown 共用失败诊断。
 *
 * @param {(desktop: object) => Promise<void>} use Playwright 测试正文回调。
 * @param {object} testInfo Playwright 当前用例的输出目录、标题和状态。
 * @param {object} operations 可替换的边界操作，仅供 support 单测注入失败。
 */
export async function executeDesktopFixture(
  use,
  testInfo,
  operations = DEFAULT_FIXTURE_OPERATIONS,
) {
  const trace = new StructuredTrace(testInfo.titlePath.join(" > "));
  const diagnostics = new FailureDiagnostics();
  let environment = null;
  let desktop = null;
  let primaryError = null;
  let diagnosticSnapshot = null;
  const teardownErrors = [];

  try {
    await operations.assertBuildReady();
    environment = await operations.createEnvironment(slug(testInfo.title));
    desktop = await operations.launchApplication(environment, trace);
    diagnostics.attach(desktop.page);
    await use(desktop);
  } catch (error) {
    primaryError = error;
  } finally {
    try {
      diagnosticSnapshot = await diagnostics.capture({
        page: desktop?.page ?? null,
        api: desktop?.api ?? null,
        environment,
      });
    } catch (error) {
      diagnostics.detach();
      teardownErrors.push(error);
    }
    try {
      if (desktop && !desktop.exited) await desktop.quit();
    } catch (error) {
      teardownErrors.push(error);
    }
    try {
      await trace.write(testInfo.outputPath("summary.json"));
    } catch (error) {
      teardownErrors.push(error);
    }
    try {
      if (environment) {
        await environment.dispose({
          preserve:
            process.env.TROWEL_E2E_PRESERVE_FAILURE === "1" &&
            hasFixtureFailure(primaryError, teardownErrors, testInfo),
        });
      }
    } catch (error) {
      teardownErrors.push(error);
    }

    let failureSummaryWritten = false;
    if (hasFixtureFailure(primaryError, teardownErrors, testInfo)) {
      failureSummaryWritten = await writeFailureSummary(
        diagnostics,
        testInfo,
        diagnosticSnapshot,
        primaryError,
        teardownErrors,
      );
    }
    try {
      await operations.auditArtifacts(testInfo.outputDir, {
        allowedFiles: failureSummaryWritten
          ? ["summary.json", "failure-summary.json"]
          : ["summary.json"],
      });
    } catch (error) {
      teardownErrors.push(error);
      if (!failureSummaryWritten) {
        failureSummaryWritten = await writeFailureSummary(
          diagnostics,
          testInfo,
          diagnosticSnapshot,
          primaryError,
          teardownErrors,
        );
        if (failureSummaryWritten) {
          try {
            await operations.auditArtifacts(testInfo.outputDir, {
              allowedFiles: ["summary.json", "failure-summary.json"],
            });
          } catch (retryError) {
            teardownErrors.push(retryError);
          }
        }
      }
    }
  }

  throwFixtureErrors(primaryError, teardownErrors);
}

const DEFAULT_FIXTURE_OPERATIONS = Object.freeze({
  assertBuildReady: assertDesktopBuildReady,
  createEnvironment: createDesktopTestEnvironment,
  launchApplication: launchDesktopApplication,
  auditArtifacts: removeUnexpectedPlaywrightArtifacts,
});

/** 判断当前 fixture 是否已经由任意阶段确定为失败。 */
function hasFixtureFailure(primaryError, teardownErrors, testInfo) {
  return (
    primaryError !== null ||
    teardownErrors.length > 0 ||
    testInfo.status !== testInfo.expectedStatus
  );
}

/** 写出一次失败摘要；写入失败本身也进入 teardown 错误集合。 */
async function writeFailureSummary(
  diagnostics,
  testInfo,
  snapshot,
  primaryError,
  teardownErrors,
) {
  try {
    const errors = primaryError === null
      ? [...teardownErrors]
      : [primaryError, ...teardownErrors];
    await diagnostics.write(testInfo.outputPath("failure-summary.json"), {
      snapshot,
      failureCategories: classifyFailureErrors(errors),
    });
    return true;
  } catch (error) {
    teardownErrors.push(error);
    return false;
  }
}

/** 保留单一原始错误，多阶段同时失败时用 AggregateError 完整交接。 */
function throwFixtureErrors(primaryError, teardownErrors) {
  const errors = primaryError === null
    ? [...teardownErrors]
    : [primaryError, ...teardownErrors];
  if (errors.length === 0) return;
  if (errors.length === 1) throw errors[0];
  throw new AggregateError(errors, "desktop E2E fixture failed in multiple stages");
}
