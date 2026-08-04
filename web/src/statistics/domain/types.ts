/** 定义统计页签、时间窗、质量和 telemetry read model。 */

import type {
  TelemetryComponent,
  TelemetryOperation,
} from "../../../shared/telemetry-contracts";

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

export type AgentRuntime = "claude_code" | "codex";
export type AgentRuntimeFilter = "all" | AgentRuntime;
export type AgentSessionStatus =
  | "completed"
  | "running"
  | "interrupted"
  | "failed"
  | "unknown";

export interface AgentTokenUsage {
  readonly input: number | null;
  readonly output: number | null;
  readonly cache_read: number | null;
  readonly cache_creation: number | null;
  readonly reasoning: number | null;
  readonly unknown: number | null;
  readonly total: number | null;
  readonly total_includes_cache_input: boolean;
  readonly known_session_count: number;
  readonly session_count: number;
  readonly quality: StatisticsQuality;
}

export interface AgentLatencyDistribution {
  readonly sample_size: number;
  readonly p50_ms: number | null;
  readonly p95_ms: number | null;
  readonly p99_ms: number | null;
  readonly quality: StatisticsQuality;
}

export interface AgentStatusCounts {
  readonly completed: number;
  readonly running: number;
  readonly interrupted: number;
  readonly failed: number;
  readonly unknown: number;
}

export interface AgentActivity {
  readonly session_sum_ms: number;
  readonly concurrent_union_ms: number;
  readonly quality: StatisticsQuality;
}

export interface AgentModelSummary {
  readonly runtime: AgentRuntime;
  readonly model: string | null;
  readonly session_count: number;
  readonly statuses: AgentStatusCounts;
  readonly tokens: AgentTokenUsage;
  readonly first_visible_response: AgentLatencyDistribution;
  readonly cache_input_ratio: number | null;
  readonly activity_ms: number;
  readonly quality: StatisticsQuality;
}

export interface AgentSession {
  readonly session_id: string;
  readonly runtime: AgentRuntime;
  readonly models: readonly string[];
  readonly started_at: string;
  readonly activity_ms: number;
  readonly first_visible_response: AgentLatencyDistribution;
  readonly tokens: AgentTokenUsage;
  readonly status: AgentSessionStatus;
  readonly quality: StatisticsQuality;
}

export interface AgentStatistics {
  readonly generated_at: string;
  readonly window_start: string;
  readonly window_end: string;
  readonly timezone: string;
  readonly sample_size: number;
  readonly quality: StatisticsQuality;
  readonly freshness: Readonly<Record<string, SourceFreshness>>;
  readonly statuses: AgentStatusCounts;
  readonly tokens: AgentTokenUsage;
  readonly first_visible_response: AgentLatencyDistribution;
  readonly activity: AgentActivity;
  readonly cache_input_ratio: number | null;
  readonly model_summaries: readonly AgentModelSummary[];
  readonly sessions: readonly AgentSession[];
}

export interface MemoryRatio {
  readonly numerator: number;
  readonly denominator: number;
  readonly ratio: number | null;
  readonly quality: StatisticsQuality;
}

export interface MemoryAttribution {
  readonly attributed: number;
  readonly unattributed: number;
  readonly coverage: MemoryRatio;
  readonly quality: StatisticsQuality;
}

export interface MemoryRetrieval {
  readonly search_calls: number;
  readonly nonempty_search_calls: number;
  readonly empty_search_calls: number;
  readonly search_hits: number;
  readonly reads: number;
  readonly read_sessions: number;
  readonly read_rate: MemoryRatio;
  readonly quality: StatisticsQuality;
}

export interface MemoryEffect {
  readonly helpful: number;
  readonly harmful: number;
  readonly unused: number;
  readonly unknown: number;
  readonly judged_user_sessions: number;
  readonly eligible_user_sessions: number;
  readonly judgement_coverage: MemoryRatio;
  readonly helpful_rate: MemoryRatio;
  readonly quality: StatisticsQuality;
}

export interface MemoryRecall {
  readonly retrieval_miss: number;
  readonly awareness_miss: number;
  readonly judged_user_sessions: number;
  readonly miss_rate: MemoryRatio;
  readonly quality: StatisticsQuality;
}

export interface MemoryAssets {
  readonly as_of: string;
  readonly active_notes: number;
  readonly raw_reads: number;
  readonly raw_harmful_outcomes: number;
  readonly contradicted_or_superseded: number;
  readonly harmful_high_notes: number;
  readonly dictionary_status: "consistent" | "stale" | "missing";
  readonly dictionary_updated_at: string | null;
  readonly quality: StatisticsQuality;
}

export interface MemorySource {
  readonly updated_at: string | null;
  readonly sample_start: string | null;
  readonly sample_end: string | null;
  readonly sample_size: number;
  readonly unknown_time_records: number;
  readonly quality: StatisticsQuality;
}

export interface MemoryStatistics {
  readonly generated_at: string;
  readonly window_start: string;
  readonly window_end: string;
  readonly timezone: string;
  readonly sample_size: number;
  readonly quality: StatisticsQuality;
  readonly freshness: Readonly<Record<string, SourceFreshness>>;
  readonly sources: Readonly<Record<string, MemorySource>>;
  readonly attribution: MemoryAttribution;
  readonly retrieval: MemoryRetrieval;
  readonly effect: MemoryEffect;
  readonly recall: MemoryRecall;
  readonly assets: MemoryAssets;
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

export interface RuntimeDistribution {
  readonly operation: string;
  readonly label: string;
  readonly sample_size: number;
  readonly error_count: number;
  readonly p50_ms: number | null;
  readonly p95_ms: number | null;
  readonly p99_ms: number | null;
  readonly quality: StatisticsQuality;
}

export interface RuntimeGauge {
  readonly value: number | null;
  readonly unit: "ms" | "By" | "1";
  readonly observed_at: string | null;
  readonly sample_size: number;
  readonly quality: StatisticsQuality;
}

export interface SidecarStatistics {
  readonly uptime: RuntimeGauge;
  readonly rss: RuntimeGauge;
  readonly restart_count: number;
  readonly abnormal_exit_count: number;
  readonly rss_series: readonly RuntimeGaugePoint[];
}

export interface RuntimeGaugePoint {
  readonly bucket_start: string;
  readonly minimum: number;
  readonly maximum: number;
  readonly average: number;
  readonly sample_size: number;
}

export interface RuntimeConnectionStatistics {
  readonly connect_count: number;
  readonly disconnect_count: number;
  readonly reconnect_count: number;
  readonly operations: readonly RuntimeDistribution[];
  readonly quality: StatisticsQuality;
}

export interface DatabaseFileStatistics {
  readonly name: "sessions.db" | "workspaces.db" | "telemetry.db";
  readonly owner: string;
  readonly database_bytes: number;
  readonly wal_bytes: number;
  readonly shm_bytes: number;
  readonly total_bytes: number;
  readonly quality: StatisticsQuality;
}

export interface SQLiteStatistics {
  readonly busy_count: number;
  readonly locked_count: number;
  readonly operations: readonly RuntimeDistribution[];
  readonly files: readonly DatabaseFileStatistics[];
  readonly quality: StatisticsQuality;
}

export interface RuntimeGap {
  readonly code: string;
  readonly message: string;
}

export interface RuntimeStatistics {
  readonly generated_at: string;
  readonly window_start: string;
  readonly window_end: string;
  readonly timezone: string;
  readonly sample_size: number;
  readonly quality: StatisticsQuality;
  readonly freshness: Readonly<Record<string, SourceFreshness>>;
  readonly resolution: StatisticsResolution;
  readonly sidecar: SidecarStatistics;
  readonly last_clean_exit_at: string | null;
  readonly lifecycle: readonly RuntimeDistribution[];
  readonly fastapi: readonly RuntimeDistribution[];
  readonly sse: RuntimeConnectionStatistics;
  readonly sqlite: SQLiteStatistics;
  readonly resources: readonly RuntimeDistribution[];
  readonly resource_remaining_count: number;
  readonly gaps: readonly RuntimeGap[];
}

export type CallComponent = TelemetryComponent;
export type CallStatus = "ok" | "error" | "unset";

export interface CallFilters {
  readonly component: "all" | CallComponent;
  readonly operation: "all" | TelemetryOperation;
  readonly runtime: AgentRuntimeFilter;
  readonly status: "all" | CallStatus;
  readonly minimumDurationMs: number;
}

export interface CallListItem {
  readonly trace_id: string;
  readonly span_id: string;
  readonly started_at: string;
  readonly duration_ms: number;
  readonly status: CallStatus;
  readonly component: CallComponent;
  readonly operation: TelemetryOperation;
  readonly runtime: AgentRuntime | null;
  readonly quality: StatisticsQuality;
}

export interface CallList {
  readonly generated_at: string;
  readonly window_start: string;
  readonly window_end: string;
  readonly timezone: string;
  readonly sample_size: number;
  readonly quality: StatisticsQuality;
  readonly freshness: Readonly<Record<string, SourceFreshness>>;
  readonly items: readonly CallListItem[];
  readonly next_cursor: string | null;
}

export interface CallTraceLink {
  readonly trace_id: string;
  readonly span_id: string | null;
  readonly available: boolean;
}

export interface CallSpan {
  readonly trace_id: string;
  readonly span_id: string;
  readonly parent_span_id: string | null;
  readonly links: readonly CallTraceLink[];
  readonly component: CallComponent;
  readonly operation: TelemetryOperation;
  readonly started_at: string;
  readonly ended_at: string;
  readonly duration_ms: number;
  readonly status: CallStatus;
  readonly runtime: AgentRuntime | null;
  readonly attributes: Readonly<Record<string, number>>;
}

export interface UnavailableInterval {
  readonly code: string;
  readonly reason: string;
  readonly source_span_id: string | null;
  readonly started_at: string | null;
  readonly ended_at: string | null;
}

export interface CallDetail {
  readonly generated_at: string;
  readonly trace_id: string;
  readonly started_at: string;
  readonly ended_at: string;
  readonly root_operation: TelemetryOperation;
  readonly status: CallStatus;
  readonly quality: StatisticsQuality;
  readonly sample_size: number;
  readonly freshness: Readonly<Record<string, SourceFreshness>>;
  readonly spans: readonly CallSpan[];
  readonly unavailable: readonly UnavailableInterval[];
}

export interface ApiEnvelope<T> {
  readonly success: boolean;
  readonly data: T | null;
  readonly error: string | null;
}
