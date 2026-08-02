/** 协调 sidecar 启动、renderer 加载和诊断状态，不持有产品业务数据。 */

import type {
  DesktopContext,
  DesktopDiagnosticState,
} from "../shared/desktop-contracts";
import {
  launchSidecar,
  SidecarStartError,
  type RunningSidecar,
  type SidecarStartOptions,
} from "./sidecar";
import {
  shutdownSidecar,
  type SidecarShutdownResult,
} from "./shutdown";

export interface DesktopHostPorts {
  readonly launch?: (options: SidecarStartOptions) => Promise<RunningSidecar>;
  readonly shutdown?: (
    running: RunningSidecar,
    options: SidecarStartOptions,
  ) => Promise<SidecarShutdownResult>;
  readonly loadRenderer: () => Promise<void> | void;
  readonly loadDiagnostics: () => Promise<void> | void;
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
  private diagnosticState: DesktopDiagnosticState;

  constructor(options: SidecarStartOptions, ports: DesktopHostPorts) {
    this.options = options;
    this.ports = {
      launch: ports.launch ?? launchSidecar,
      shutdown: ports.shutdown ?? shutdownSidecar,
      loadRenderer: ports.loadRenderer,
      loadDiagnostics: ports.loadDiagnostics,
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
    if (!this.startPromise) {
      this.startPromise = this.startAttempt().finally(() => {
        this.startPromise = null;
      });
    }
    return this.startPromise;
  }

  async retry(): Promise<void> {
    if (this.running) await this.stop();
    this.stopPromise = null;
    await this.start();
  }

  stop(): Promise<SidecarShutdownResult> {
    if (this.stopPromise) return this.stopPromise;
    this.stopping = true;
    this.generation += 1;
    this.stopPromise = this.stopAttempt();
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
      await this.ports.loadRenderer();
      // 先完成 renderer 加载再订阅已可兑现的退出 Promise，确保诊断页最后落在前台。
      this.watchCurrentProcess(launched, generation);
    } catch (error) {
      if (generation !== this.generation || this.stopping) return;
      if (launched) await this.shutdownLaunched(launched);
      this.running = null;
      this.diagnosticState = diagnosticFromError(
        error,
        this.options.logDirectory,
      );
      await this.ports.loadDiagnostics();
    }
  }

  private async stopAttempt(): Promise<SidecarShutdownResult> {
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
      };
    }
    const result = await this.shutdownLaunched(running);
    if (this.running === running) this.running = null;
    return result;
  }

  private async shutdownLaunched(
    running: RunningSidecar,
  ): Promise<SidecarShutdownResult> {
    /** 统一处理正常退出、启动取消和 renderer 加载失败后的 sidecar。 */
    try {
      return await this.ports.shutdown(running, this.options);
    } catch {
      running.process.stop();
      return {
        status: "needs_reconcile",
        remainingResourceCount: 1,
        forced: true,
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
      this.running = null;
      this.diagnosticState = {
        status: "failed",
        category: "early_exit",
        message: "Python sidecar exited while Trowel was running.",
        exitCode: exit.code,
        logDirectory: this.options.logDirectory,
      };
      await this.ports.loadDiagnostics();
    });
  }
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
