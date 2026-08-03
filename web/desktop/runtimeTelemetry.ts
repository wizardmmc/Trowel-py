/** 集中记录 Electron Host 的启动、窗口、renderer 和 sidecar 运行事实。 */

import { randomBytes, randomUUID } from "node:crypto";
import type { TelemetryBatcher } from "../shared/telemetry-batcher";
import type {
  TelemetryAttributes,
  TelemetryOperation,
} from "../shared/telemetry-contracts";

export class DesktopRuntimeTelemetry {
  private firstScreenRecorded = false;

  public constructor(
    private readonly batcher: TelemetryBatcher,
    private readonly applicationStartedAt: Date,
  ) {}

  /** 记录应用启动到 sidecar readiness 握手完成。 */
  public recordSidecarReady(completedAt: Date): void {
    this.recordSpan(
      "desktop.start.sidecar_ready",
      this.applicationStartedAt,
      completedAt,
      "ok",
      { quality: "reliable", transport: "http" },
    );
  }

  /** 首屏标记只接受第一次成功渲染，恢复重载不混入冷启动分布。 */
  public recordFirstScreen(completedAt: Date): void {
    if (this.firstScreenRecorded) return;
    this.firstScreenRecorded = true;
    this.recordSpan(
      "desktop.start.first_screen",
      this.applicationStartedAt,
      completedAt,
      "ok",
      { quality: "reliable" },
    );
  }

  /** 记录 renderer 非 clean-exit 终态，不保存 Electron 错误正文。 */
  public recordRendererCrash(observedAt: Date): void {
    this.recordSpan(
      "desktop.renderer.crash",
      observedAt,
      observedAt,
      "error",
      { quality: "reliable", error_category: "crash" },
    );
  }

  /** 窗口关闭单独成组，不与整个应用退出耗时混算。 */
  public recordWindowClose(observedAt: Date): void {
    this.recordSpan(
      "desktop.window.close",
      observedAt,
      observedAt,
      "ok",
      { quality: "reliable" },
    );
  }

  /** sidecar 第二次及之后 ready 时累计一次重启。 */
  public recordSidecarRestart(observedAt: Date): void {
    this.batcher.recordMetric({
      metric_id: `sidecar-restart-${randomUUID()}`,
      observed_at: observedAt.toISOString(),
      component: "electron",
      name: "sidecar.restart",
      kind: "counter",
      unit: "1",
      value: 1,
      status: "ok",
      runtime: null,
      model: null,
      operation: "desktop.start.sidecar_ready",
      attributes: { quality: "reliable" },
    });
  }

  /** 有界排空当前 Host 队列；超时由 batcher 自行计入 dropped。 */
  public drain(timeoutMs: number) {
    return this.batcher.drain(timeoutMs);
  }

  /** 主动发送当前小批次，不等待定时器。 */
  public flush(): Promise<void> {
    return this.batcher.flush();
  }

  /** 构造一条不带 session、调用或动态路径的 Host span。 */
  private recordSpan(
    operation: TelemetryOperation,
    startedAt: Date,
    endedAt: Date,
    status: "ok" | "error" | "unset",
    attributes: TelemetryAttributes,
  ): void {
    this.batcher.recordSpan({
      trace_id: randomBytes(16).toString("hex"),
      span_id: randomBytes(8).toString("hex"),
      parent_span_id: null,
      started_at: startedAt.toISOString(),
      ended_at: endedAt.toISOString(),
      component: "electron",
      operation,
      status,
      runtime: null,
      model: null,
      session_ref: null,
      call_ref: null,
      attributes,
      links: [],
    });
  }
}
