/** 协调 sidecar 启动、renderer 加载和诊断状态，不持有产品业务数据。 */

import type {
  DesktopContext,
  DesktopDiagnosticState,
} from "../shared/desktop-contracts";
import {
  launchSidecar,
  probeSidecarReadiness,
  SidecarStartError,
  type RunningSidecar,
  type SidecarStartOptions,
} from "./sidecar";
import {
  shutdownSidecar,
  type SidecarShutdownReason,
  type SidecarShutdownResult,
} from "./shutdown";

export interface DesktopHostPorts {
  readonly launch?: (options: SidecarStartOptions) => Promise<RunningSidecar>;
  readonly shutdown?: (
    running: RunningSidecar,
    options: SidecarStartOptions,
    reason: SidecarShutdownReason,
  ) => Promise<SidecarShutdownResult>;
  readonly loadRenderer: () => Promise<void> | void;
  readonly loadDiagnostics: () => Promise<void> | void;
  readonly onSidecarReady?: (running: RunningSidecar) => void;
  readonly onUnexpectedExit?: (exit: {
    readonly code: number | null;
    readonly signal: string | null;
  }) => void;
  readonly probeReadiness?: (
    running: RunningSidecar,
    options: SidecarStartOptions,
  ) => Promise<boolean>;
  readonly scheduleHealthCheck?: (
    check: () => Promise<void>,
    delayMs: number,
  ) => () => void;
}

export class DesktopHost {
  private readonly options: SidecarStartOptions;
  private readonly ports: Required<DesktopHostPorts>;
  private running: RunningSidecar | null = null;
  private generation = 0;
  private startPromise: Promise<void> | null = null;
  private stopPromise: Promise<SidecarShutdownResult> | null = null;
  private lateShutdownResult: SidecarShutdownResult | null = null;
  private stopping = false;
  private cancelHealthCheck: (() => void) | null = null;
  private consecutiveReadinessFailures = 0;
  private reconciliationRequired = false;
  private reconcileDisplayPromise: Promise<void> | null = null;
  private diagnosticState: DesktopDiagnosticState;

  constructor(options: SidecarStartOptions, ports: DesktopHostPorts) {
    this.options = options;
    this.ports = {
      launch: ports.launch ?? launchSidecar,
      shutdown: ports.shutdown ?? shutdownSidecar,
      loadRenderer: ports.loadRenderer,
      loadDiagnostics: ports.loadDiagnostics,
      onSidecarReady: ports.onSidecarReady ?? (() => undefined),
      onUnexpectedExit: ports.onUnexpectedExit ?? (() => undefined),
      probeReadiness: ports.probeReadiness ?? probeSidecarReadiness,
      scheduleHealthCheck: ports.scheduleHealthCheck ?? scheduleHealthCheck,
    };
    this.diagnosticState = {
      status: "starting",
      category: null,
      message: null,
      exitCode: null,
      logDirectory: options.logDirectory,
    };
  }

  start(): Promise<void> {
    if (this.reconciliationRequired) return this.showReconcileRequired();
    if (!this.startPromise) {
      this.startPromise = this.startAttempt().finally(() => {
        this.startPromise = null;
      });
    }
    return this.startPromise;
  }

  async retry(): Promise<void> {
    const pendingShutdown =
      this.stopPromise ?? (this.running ? this.stop("sidecar_abnormal") : null);
    if (pendingShutdown) {
      const result = await pendingShutdown;
      if (result.status === "needs_reconcile") {
        await this.showReconcileRequired();
        return;
      }
    }
    this.stopPromise = null;
    this.reconciliationRequired = false;
    this.consecutiveReadinessFailures = 0;
    await this.start();
  }

  stop(
    reason: SidecarShutdownReason = "app_exit",
  ): Promise<SidecarShutdownResult> {
    if (this.stopPromise) return this.stopPromise;
    this.disarmHealthMonitor();
    this.stopping = true;
    this.generation += 1;
    this.stopPromise = this.stopAttempt(reason);
    return this.stopPromise;
  }

  context(): DesktopContext {
    if (!this.running) throw new Error("desktop sidecar is not ready");
    return {
      environment: "desktop",
      appVersion: this.options.expectedAppVersion,
      instanceId: this.options.instanceId,
      transport: this.running.transport,
    };
  }

  diagnostics(): DesktopDiagnosticState {
    return this.diagnosticState;
  }

  private async startAttempt(): Promise<void> {
    this.stopping = false;
    this.lateShutdownResult = null;
    this.consecutiveReadinessFailures = 0;
    this.reconcileDisplayPromise = null;
    const generation = ++this.generation;
    this.diagnosticState = {
      ...this.diagnosticState,
      status: "starting",
      category: null,
      message: null,
      exitCode: null,
    };
    let launched: RunningSidecar | null = null;
    try {
      launched = await this.ports.launch(this.options);
      if (generation !== this.generation || this.stopping) {
        this.lateShutdownResult = await this.shutdownLaunched(launched);
        return;
      }
      this.running = launched;
      this.diagnosticState = {
        ...this.diagnosticState,
        status: "ready",
      };
      this.ports.onSidecarReady(launched);
      await this.ports.loadRenderer();
      // 先完成 renderer 加载再订阅已可兑现的退出 Promise，确保诊断页最后落在前台。
      this.watchCurrentProcess(launched, generation);
      this.armHealthMonitor(launched, generation);
    } catch (error) {
      if (generation !== this.generation || this.stopping) return;
      let cleanupResult =
        error instanceof SidecarStartError ? error.cleanupResult : null;
      if (launched) {
        cleanupResult = await this.shutdownLaunched(
          launched,
          "sidecar_abnormal",
        );
      }
      this.running = null;
      this.disarmHealthMonitor();
      if (cleanupResult?.status === "needs_reconcile") {
        this.stopPromise = Promise.resolve(cleanupResult);
        await this.showReconcileRequired();
        return;
      }
      this.diagnosticState = diagnosticFromError(
        error,
        this.options.logDirectory,
      );
      await this.ports.loadDiagnostics();
    }
  }

  private async stopAttempt(
    reason: SidecarShutdownReason,
  ): Promise<SidecarShutdownResult> {
    /** 启动与退出交错时，先等启动路径把迟到 sidecar 完整收敛。 */
    const pendingStart = this.startPromise;
    if (pendingStart) await pendingStart;
    if (this.lateShutdownResult) return this.lateShutdownResult;
    const running = this.running;
    if (!running) {
      return {
        status: "closed",
        remainingResourceCount: 0,
        forced: false,
        exitMarkerRecorded: false,
      };
    }
    const result = await this.shutdownLaunched(running, reason);
    if (this.running === running) this.running = null;
    return result;
  }

  private async shutdownLaunched(
    running: RunningSidecar,
    reason: SidecarShutdownReason = "app_exit",
  ): Promise<SidecarShutdownResult> {
    /** 统一处理正常退出、启动取消和 renderer 加载失败后的 sidecar。 */
    try {
      return await this.ports.shutdown(running, this.options, reason);
    } catch {
      running.process.stop();
      return {
        status: "needs_reconcile",
        remainingResourceCount: 1,
        forced: true,
        exitMarkerRecorded: false,
      };
    }
  }

  private watchCurrentProcess(
    running: RunningSidecar,
    generation: number,
  ): void {
    void running.process.exited.then(async (exit) => {
      if (
        generation !== this.generation ||
        this.stopping ||
        this.running !== running
      ) {
        return;
      }
      this.disarmHealthMonitor();
      this.diagnosticState = {
        status: "failed",
        category: "early_exit",
        message: "Python sidecar exited while Trowel was running.",
        exitCode: exit.code,
        logDirectory: this.options.logDirectory,
      };
      this.ports.onUnexpectedExit(exit);
      const shutdown = this.stop("sidecar_abnormal");
      await this.ports.loadDiagnostics();
      const result = await shutdown;
      if (result.status === "needs_reconcile") {
        await this.showReconcileRequired();
      }
    });
  }

  private armHealthMonitor(running: RunningSidecar, generation: number): void {
    /** 串行调度 readiness，避免上一次超时时又堆积新请求。 */
    this.disarmHealthMonitor();
    const intervalMs = this.options.livenessIntervalMs ?? 2_000;
    this.cancelHealthCheck = this.ports.scheduleHealthCheck(async () => {
      this.cancelHealthCheck = null;
      if (
        generation !== this.generation ||
        this.stopping ||
        this.running !== running
      ) {
        return;
      }
      let ready = false;
      try {
        ready = await this.ports.probeReadiness(running, this.options);
      } catch {
        // 端口实现异常与 readiness 请求失败具有相同的存活语义。
      }
      if (
        generation !== this.generation ||
        this.stopping ||
        this.running !== running
      ) {
        return;
      }
      this.consecutiveReadinessFailures = ready
        ? 0
        : this.consecutiveReadinessFailures + 1;
      const threshold = this.options.livenessFailureThreshold ?? 3;
      if (this.consecutiveReadinessFailures < threshold) {
        this.armHealthMonitor(running, generation);
        return;
      }
      this.diagnosticState = {
        status: "failed",
        category: "readiness_lost",
        message:
          "Agent Service 进程仍在，但已停止响应。Trowel 正在收敛旧进程，点击重试会启动新服务。",
        exitCode: null,
        logDirectory: this.options.logDirectory,
      };
      const shutdown = this.stop("sidecar_abnormal");
      await this.ports.loadDiagnostics();
      const result = await shutdown;
      if (result.status === "needs_reconcile") {
        await this.showReconcileRequired();
      }
    }, intervalMs);
  }

  private disarmHealthMonitor(): void {
    /** 取消当前存活定时器，不影响已经在执行的单次检查。 */
    this.cancelHealthCheck?.();
    this.cancelHealthCheck = null;
  }

  private showReconcileRequired(): Promise<void> {
    /** 保留旧快照，阻止同一实例的新 sidecar 覆盖尚未收敛的资源事实。 */
    this.reconciliationRequired = true;
    if (this.reconcileDisplayPromise) return this.reconcileDisplayPromise;
    this.diagnosticState = {
      status: "failed",
      category: "reconcile_required",
      message:
        "旧 Agent Service 的资源尚未完全收敛。为保留恢复快照，Trowel 已阻止直接重试；请退出并重新打开 Trowel。",
      exitCode: null,
      logDirectory: this.options.logDirectory,
    };
    this.reconcileDisplayPromise = Promise.resolve()
      .then(() => this.ports.loadDiagnostics())
      .catch(() => undefined);
    return this.reconcileDisplayPromise;
  }
}

function scheduleHealthCheck(
  check: () => Promise<void>,
  delayMs: number,
): () => void {
  /** 用不阻止 Electron 退出的单次定时器调度下一次存活检查。 */
  const timer = setTimeout(() => {
    void check().catch(() => undefined);
  }, delayMs);
  timer.unref();
  return () => clearTimeout(timer);
}

function diagnosticFromError(
  error: unknown,
  logDirectory: string,
): DesktopDiagnosticState {
  if (error instanceof SidecarStartError) {
    return {
      status: "failed",
      category: error.category,
      message: error.message,
      exitCode: error.exitCode,
      logDirectory,
    };
  }
  return {
    status: "failed",
    category: "early_exit",
    message: "Python sidecar could not be started.",
    exitCode: null,
    logDirectory,
  };
}
