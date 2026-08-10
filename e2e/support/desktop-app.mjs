/** 用 Playwright 驱动真实 Electron，并统一收集事实与执行最终资源核验。 */

import { _electron as electron } from "playwright";
import { assertApplicationResourcesClosed } from "./resource-assertions.mjs";
import { waitForDesktopApi } from "./api-client.mjs";
import { terminateVerifiedProcessGroups } from "./process-groups.mjs";

/** 持有一条真实 Electron、renderer 和私有 sidecar 连续会话。 */
export class DesktopApplication {
  /**
   * @param {object} options 本次运行的所有外部依赖。
   * @param {import("playwright").ElectronApplication} options.electronApp Electron 驱动。
   * @param {import("playwright").Page} options.page 主窗口 renderer。
   * @param {import("./desktop-environment.mjs").DesktopTestEnvironment} options.environment 隔离环境。
   * @param {import("./api-client.mjs").DesktopApiClient} options.api 当前 sidecar client。
   * @param {import("./artifacts.mjs").StructuredTrace} options.trace 结构化步骤 trace。
   * @param {number} [options.quitTimeoutMs] App Quit 请求和退出共用的有界等待预算。
   */
  constructor({
    electronApp,
    page,
    environment,
    api,
    trace,
    quitTimeoutMs = 25_000,
  }) {
    this.electronApp = electronApp;
    this.page = page;
    this.environment = environment;
    this.api = api;
    this.trace = trace;
    this.quitTimeoutMs = quitTimeoutMs;
    this.exited = false;
  }

  /** 点击 macOS 窗口关闭动作，并确认应用只隐藏窗口。 */
  async closeWindow() {
    await this.electronApp.evaluate(({ BrowserWindow }) => {
      BrowserWindow.getAllWindows()[0]?.close();
    });
    await waitForMainProcessValue(
      this.electronApp,
      ({ BrowserWindow }) => BrowserWindow.getAllWindows()[0]?.isVisible() === false,
    );
    this.trace.record("window.hidden");
  }

  /** 处理全新 Garden 数据可能触发的真实奖励弹窗，防止它遮住全局导航。 */
  async dismissStartupOverlay() {
    const notification = this.page.getByRole("dialog", { name: "事件通知" });
    if (!(await notification.isVisible().catch(() => false))) return;
    await notification.getByRole("button", { name: /领取奖励/ }).click();
    await notification.waitFor({ state: "hidden" });
    this.trace.record("startup.notification_claimed");
  }

  /** 模拟 macOS Dock 重新激活，并确认原窗口重新可见。 */
  async reopenWindow() {
    await this.electronApp.evaluate(({ app }) => app.emit("activate"));
    await waitForMainProcessValue(
      this.electronApp,
      ({ BrowserWindow }) => BrowserWindow.getAllWindows()[0]?.isVisible() === true,
    );
    await this.page.waitForLoadState("domcontentloaded");
    this.trace.record("window.reopened");
  }

  /**
   * 强制终止当前 Electron 进程，再用同一隔离数据根启动新实例。
   *
   * 该路径故意不执行 App Quit，供恢复测试观察上一次进程留下的租约和 attempt；
   * readiness 必须来自轮换后的 Host credential，不能误连旧 sidecar。
   */
  async crashAndRestart() {
    const previousCredential = this.api.credential;
    const resourceSnapshot = await this.environment.readResourceSnapshot();
    const electronProcess = this.electronApp.process();
    electronProcess.kill("SIGKILL");
    await waitForChildExit(
      electronProcess,
      10_000,
      "Electron did not crash before deadline",
    );
    await terminateVerifiedProcessGroups(resourceSnapshot, {
      expectedAppInstanceIdentity: this.api.appInstanceIdentity,
      expectedDataRootIdentity: this.environment.dataRootIdentity,
      initialSignal: "SIGKILL",
      graceMs: 5_000,
    });
    this.exited = true;
    this.trace.record("application.crashed");

    const restarted = await launchDesktopApplication(this.environment, this.trace, {
      previousCredential,
    });
    this.electronApp = restarted.electronApp;
    this.page = restarted.page;
    this.api = restarted.api;
    this.exited = false;
    this.trace.record("application.restarted");
  }

  /** 触发真正的 App Quit，等待进程退出并核验 Host 最终 marker。 */
  async quit() {
    if (this.exited) return this.environment.verifyCleanExit();
    const child = this.electronApp.process();
    let gracefulError = null;
    let quitDeadline = null;
    try {
      await Promise.race([
        (async () => {
          await this.electronApp.evaluate(({ app }) => app.quit());
          await waitForChildExit(
            child,
            this.quitTimeoutMs,
            "Electron did not quit before deadline",
          );
        })(),
        new Promise((_, reject) => {
          quitDeadline = setTimeout(
            () => reject(new Error("Electron quit request did not finish before deadline")),
            this.quitTimeoutMs,
          );
        }),
      ]);
    } catch (error) {
      gracefulError = error;
      child.kill("SIGKILL");
      await waitForChildExit(
        child,
        10_000,
        "Electron did not exit after forced teardown",
      );
    } finally {
      if (quitDeadline !== null) clearTimeout(quitDeadline);
    }
    this.exited = true;
    if (gracefulError) throw gracefulError;
    const marker = await this.environment.verifyCleanExit();
    assertApplicationResourcesClosed(marker);
    this.trace.record("application.closed", {
      status: marker.status,
      remaining_resource_count: marker.remaining_resource_count,
    });
    return marker;
  }
}

/** 启动编译后的真实 Trowel Electron 和 Python sidecar。 */
export async function launchDesktopApplication(
  environment,
  trace,
  { previousCredential = null } = {},
) {
  const electronApp = await electron.launch({
    executablePath: environment.electronBinary,
    args: [environment.webRoot],
    cwd: environment.webRoot,
    env: environment.electronEnv,
    timeout: 30_000,
  });
  try {
    const page = await electronApp.firstWindow({ timeout: 30_000 });
    const api = await waitForDesktopApi(environment.serviceDescriptorPath, {
      previousCredential,
    });
    environment.registerAppInstanceIdentity(api.appInstanceIdentity);
    const application = new DesktopApplication({ electronApp, page, environment, api, trace });
    const startupNotification = page.getByRole("dialog", { name: "事件通知" });
    await page.waitForLoadState("domcontentloaded");
    await page
      .locator('[data-testid="empty-garden"], [data-testid="garden-view"]')
      .first()
      .waitFor({ state: "visible", timeout: 30_000 });
    await application.dismissStartupOverlay();
    await page.addLocatorHandler(startupNotification, async () => {
      await startupNotification.getByRole("button", { name: /领取奖励/ }).click();
      trace.record("startup.notification_claimed");
    });
    trace.record("application.ready", { platform: process.platform });
    return application;
  } catch (error) {
    await forceCloseAfterLaunchFailure(electronApp);
    throw error;
  }
}

/** 启动中途失败时也确保 Electron 真正退出，不把清理责任留给后续用例。 */
async function forceCloseAfterLaunchFailure(electronApp) {
  const child = electronApp.process();
  let closeDeadline = null;
  try {
    await Promise.race([
      electronApp.close(),
      new Promise((_, reject) => {
        closeDeadline = setTimeout(
          () => reject(new Error("Electron close timed out")),
          10_000,
        );
      }),
    ]);
  } catch {
    child.kill("SIGKILL");
    await waitForChildExit(child, 10_000, "Electron launch cleanup timed out");
  } finally {
    if (closeDeadline !== null) clearTimeout(closeDeadline);
  }
}

/** 等待 Electron 子进程退出，并覆盖监听注册前已经结束的竞态。 */
async function waitForChildExit(child, timeoutMs, message) {
  if (child.exitCode !== null || child.signalCode !== null) return;
  await new Promise((resolve, reject) => {
    const onExit = () => {
      clearTimeout(deadline);
      resolve();
    };
    const deadline = setTimeout(() => {
      child.off("exit", onExit);
      reject(new Error(message));
    }, timeoutMs);
    child.once("exit", onExit);
  });
}

/** 轮询 main-process observable，不使用固定 sleep 作为场景完成条件。 */
async function waitForMainProcessValue(electronApp, predicate, timeoutMs = 5_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await electronApp.evaluate(predicate)) return;
    await new Promise((resolve) => setTimeout(resolve, 25));
  }
  throw new Error("Electron main-process condition did not become true");
}
