/** 为 Electron 与 renderer 提供有界批量、有限重试和退出 drain。 */

import type {
  TelemetryBatch,
  TelemetryComponent,
  TelemetryMetric,
  TelemetrySpan,
  TelemetrySubmitData,
} from "./telemetry-contracts";

type QueuedRecord =
  | { readonly kind: "span"; readonly value: TelemetrySpan }
  | { readonly kind: "metric"; readonly value: TelemetryMetric };

export interface TelemetryBatcherOptions {
  readonly sourceComponent: TelemetryComponent;
  readonly send: (batch: TelemetryBatch) => Promise<TelemetrySubmitData>;
  readonly batchSize?: number;
  readonly queueCapacity?: number;
  readonly flushIntervalMs?: number;
  readonly maxRetries?: number;
  readonly retryBaseMs?: number;
  readonly idFactory?: () => string;
  readonly now?: () => Date;
}

export interface TelemetryBatcherSnapshot {
  readonly accepted: number;
  readonly dropped: number;
  readonly queued: number;
  readonly inflight: number;
}

export interface TelemetryDrainReport {
  readonly drained: boolean;
  readonly dropped: number;
  readonly remaining: number;
}

export class TelemetryBatcher {
  private readonly sourceComponent: TelemetryComponent;
  private readonly send: (batch: TelemetryBatch) => Promise<TelemetrySubmitData>;
  private readonly batchSize: number;
  private readonly queueCapacity: number;
  private readonly flushIntervalMs: number;
  private readonly maxRetries: number;
  private readonly retryBaseMs: number;
  private readonly idFactory: () => string;
  private readonly now: () => Date;
  private readonly queue: QueuedRecord[] = [];
  private timer: ReturnType<typeof setTimeout> | null = null;
  private flushing: Promise<void> | null = null;
  private inflight = 0;
  private accepted = 0;
  private dropped = 0;
  private stopped = false;
  private timeoutAccounted = false;

  constructor(options: TelemetryBatcherOptions) {
    this.sourceComponent = options.sourceComponent;
    this.send = options.send;
    this.batchSize = positiveInteger(options.batchSize ?? 250, "batchSize");
    this.queueCapacity = positiveInteger(
      options.queueCapacity ?? 4096,
      "queueCapacity",
    );
    this.flushIntervalMs = positiveInteger(
      options.flushIntervalMs ?? 100,
      "flushIntervalMs",
    );
    this.maxRetries = Math.max(0, options.maxRetries ?? 2);
    this.retryBaseMs = Math.max(0, options.retryBaseMs ?? 25);
    this.idFactory = options.idFactory ?? defaultBatchId;
    this.now = options.now ?? (() => new Date());
  }

  recordSpan(span: TelemetrySpan): boolean {
    return this.enqueue({ kind: "span", value: span });
  }

  recordMetric(metric: TelemetryMetric): boolean {
    return this.enqueue({ kind: "metric", value: metric });
  }

  snapshot(): TelemetryBatcherSnapshot {
    return {
      accepted: this.accepted,
      dropped: this.dropped,
      queued: this.queue.length,
      inflight: this.inflight,
    };
  }

  async flush(): Promise<void> {
    if (this.flushing) return this.flushing;
    this.clearTimer();
    this.flushing = this.flushLoop().finally(() => {
      this.flushing = null;
      if (!this.stopped && this.queue.length > 0) this.schedule();
    });
    return this.flushing;
  }

  async drain(timeoutMs: number): Promise<TelemetryDrainReport> {
    this.stopped = true;
    this.clearTimer();
    const flush = this.flush();
    let timeout: ReturnType<typeof setTimeout> | undefined;
    const drained = await Promise.race([
      flush.then(() => true),
      new Promise<boolean>((resolve) => {
        timeout = setTimeout(() => resolve(false), Math.max(0, timeoutMs));
      }),
    ]);
    if (timeout !== undefined) clearTimeout(timeout);
    if (!drained && !this.timeoutAccounted) {
      this.dropped += this.queue.length + this.inflight;
      this.queue.splice(0);
      this.timeoutAccounted = true;
    }
    return {
      drained,
      dropped: this.dropped,
      remaining: drained ? 0 : this.queue.length + this.inflight,
    };
  }

  private enqueue(record: QueuedRecord): boolean {
    if (this.stopped || this.queue.length + this.inflight >= this.queueCapacity) {
      this.dropped += 1;
      return false;
    }
    this.queue.push(record);
    this.accepted += 1;
    if (this.queue.length >= this.batchSize) {
      void this.flush();
    } else {
      this.schedule();
    }
    return true;
  }

  private async flushLoop(): Promise<void> {
    while (this.queue.length > 0) {
      const records = this.queue.splice(0, this.batchSize);
      this.inflight = records.length;
      try {
        const result = await this.sendWithRetry(this.buildBatch(records));
        if (!this.timeoutAccounted) {
          this.dropped += result.rejected + result.dropped;
        }
      } catch {
        if (!this.timeoutAccounted) this.dropped += records.length;
      } finally {
        this.inflight = 0;
      }
      if (this.timeoutAccounted) return;
    }
  }

  private buildBatch(records: readonly QueuedRecord[]): TelemetryBatch {
    return {
      batch_id: this.idFactory(),
      schema_version: 1,
      source_component: this.sourceComponent,
      collected_at: this.now().toISOString(),
      mode: "normal",
      spans: records
        .filter((record): record is Extract<QueuedRecord, { kind: "span" }> =>
          record.kind === "span"
        )
        .map((record) => record.value),
      metrics: records
        .filter((record): record is Extract<QueuedRecord, { kind: "metric" }> =>
          record.kind === "metric"
        )
        .map((record) => record.value),
    };
  }

  private async sendWithRetry(batch: TelemetryBatch): Promise<TelemetrySubmitData> {
    for (let attempt = 0; ; attempt += 1) {
      try {
        return await this.send(batch);
      } catch (error) {
        if (attempt >= this.maxRetries) throw error;
        await delay(this.retryBaseMs * 2 ** attempt);
      }
    }
  }

  private schedule(): void {
    if (this.timer !== null) return;
    this.timer = setTimeout(() => {
      this.timer = null;
      void this.flush();
    }, this.flushIntervalMs);
  }

  private clearTimer(): void {
    if (this.timer === null) return;
    clearTimeout(this.timer);
    this.timer = null;
  }
}

function positiveInteger(value: number, name: string): number {
  if (!Number.isInteger(value) || value <= 0) {
    throw new Error(`${name} must be a positive integer`);
  }
  return value;
}

function defaultBatchId(): string {
  return `batch-${crypto.randomUUID()}`;
}

function delay(milliseconds: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}
