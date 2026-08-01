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

export interface DesktopHostPorts {
  readonly launch?: (options: SidecarStartOptions) => Promise<RunningSidecar>;
  readonly loadRenderer: () => Promise<void> | void;
  readonly loadDiagnostics: () => Promise<void> | void;
}

export class DesktopHost {
  private readonly options: SidecarStartOptions;
  private readonly ports: Required<DesktopHostPorts>;
  private running: RunningSidecar | null = null;
  private generation = 0;
  private startPromise: Promise<void> | null = null;
  private stopping = false;
  private diagnosticState: DesktopDiagnosticState;

  constructor(options: SidecarStartOptions, ports: DesktopHostPorts) {
    this.options = options;
    this.ports = {
      launch: ports.launch ?? launchSidecar,
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
    this.running?.process.stop();
    this.running = null;
    await this.start();
  }

  stop(): void {
    this.stopping = true;
    this.generation += 1;
    this.running?.process.stop();
    this.running = null;
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
        launched.process.stop();
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
      launched?.process.stop();
      this.running = null;
      this.diagnosticState = diagnosticFromError(
        error,
        this.options.logDirectory,
      );
      await this.ports.loadDiagnostics();
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
