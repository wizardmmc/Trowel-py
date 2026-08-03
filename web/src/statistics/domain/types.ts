/** 定义统计页签、时间窗、质量和 telemetry read model。 */

export type StatisticsTab =
  | "overview"
  | "agent"
  | "memory"
  | "runtime"
  | "calls";

export type StatisticsResolution = "hour" | "day";
export type StatisticsQuality = "reliable" | "partial" | "unavailable";

export interface StatisticsDateRange {
  readonly startDate: string;
  readonly endDate: string;
  readonly timezone: string;
}

export interface SourceFreshness {
  readonly updated_at: string | null;
  readonly status: "fresh" | "stale" | "unavailable";
}

export interface CollectorStatistics {
  readonly accepted: number;
  readonly rejected: number;
  readonly dropped: number;
  readonly queued_records: number;
  readonly inflight_records: number;
  readonly running: boolean;
  readonly last_error_category: string | null;
}

export interface SpanAggregate {
  readonly bucket_start: string;
  readonly component: string;
  readonly operation: string;
  readonly status: string;
  readonly runtime: string | null;
  readonly model: string | null;
  readonly sample_count: number;
  readonly duration_sum_ms: number;
  readonly duration_min_ms: number;
  readonly duration_max_ms: number;
  readonly histogram_counts: readonly number[];
}

export interface MetricAggregate {
  readonly bucket_start: string;
  readonly component: string;
  readonly name: string;
  readonly kind: string;
  readonly unit: string;
  readonly operation: string | null;
  readonly status: string;
  readonly runtime: string | null;
  readonly model: string | null;
  readonly sample_count: number;
  readonly value_sum: number;
  readonly value_min: number;
  readonly value_max: number;
}

export interface TelemetryStatistics {
  readonly generated_at: string;
  readonly window_start: string;
  readonly window_end: string;
  readonly timezone: string;
  readonly sample_size: number;
  readonly quality: StatisticsQuality;
  readonly freshness: Readonly<Record<string, SourceFreshness>>;
  readonly resolution: StatisticsResolution;
  readonly collector: CollectorStatistics;
  readonly database_bytes: Readonly<Record<string, number>>;
  readonly histogram_upper_bounds_ms: readonly (number | null)[];
  readonly spans: readonly SpanAggregate[];
  readonly metrics: readonly MetricAggregate[];
}

export interface ApiEnvelope<T> {
  readonly success: boolean;
  readonly data: T | null;
  readonly error: string | null;
}
