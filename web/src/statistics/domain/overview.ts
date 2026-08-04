/** 定义 Statistics 总览汇总、趋势、状态和会话问题类型。 */

import type {
  AgentActivity,
  AgentLatencyDistribution,
  AgentStatusCounts,
  AgentTokenUsage,
  DatabaseFileStatistics,
  MemoryRatio,
  SourceFreshness,
  StatisticsQuality,
} from "./types";

export interface OverviewAgent {
  readonly user_sessions: number;
  readonly statuses: AgentStatusCounts;
  readonly tokens: AgentTokenUsage;
  readonly first_visible_response: AgentLatencyDistribution;
  readonly activity: AgentActivity;
  readonly quality: StatisticsQuality;
}

export interface OverviewTokenTrendPoint {
  readonly date: string;
  readonly session_count: number;
  readonly known_token_session_count: number;
  readonly token_total: number | null;
  readonly quality: StatisticsQuality;
}

export interface OverviewMemory {
  readonly search_hits: number;
  readonly reads: number;
  readonly judged_effects: number;
  readonly helpful: number;
  readonly helpful_rate: MemoryRatio;
  readonly judgement_coverage: MemoryRatio;
  readonly recall_miss_rate: MemoryRatio;
  readonly attribution_coverage: MemoryRatio;
  readonly active_notes: number;
  readonly quality: StatisticsQuality;
}

export interface OverviewStatus {
  readonly code: string;
  readonly level: "info" | "warning" | "error" | "unavailable";
  readonly title: string;
  readonly detail: string;
  readonly source: string;
  readonly quality: StatisticsQuality;
  readonly freshness: SourceFreshness;
}

export interface OverviewSessionProblem {
  readonly trowel_session_id: string;
  readonly runtime: "claude_code" | "codex";
  readonly closed_at: string;
  readonly problem_text: string;
}

export interface OverviewSessionProblems {
  readonly reviewed_session_count: number;
  readonly problem_count: number;
  readonly quality: StatisticsQuality;
  readonly freshness: SourceFreshness;
  readonly items: readonly OverviewSessionProblem[];
}

export type OverviewSourceName =
  | "agent"
  | "memory"
  | "runtime"
  | "calls"
  | "session_problems";

export interface OverviewSource {
  readonly label: string;
  readonly sample_size: number;
  readonly quality: StatisticsQuality;
  readonly freshness: SourceFreshness;
}

export interface OverviewStatistics {
  readonly generated_at: string;
  readonly window_start: string;
  readonly window_end: string;
  readonly timezone: string;
  readonly sample_size: number;
  readonly quality: StatisticsQuality;
  readonly freshness: Readonly<Record<string, SourceFreshness>>;
  readonly agent: OverviewAgent;
  readonly token_trend: readonly OverviewTokenTrendPoint[];
  readonly memory: OverviewMemory;
  readonly statuses: readonly OverviewStatus[];
  readonly session_problems: OverviewSessionProblems;
  readonly database_files: readonly DatabaseFileStatistics[];
  readonly sources: Readonly<Record<OverviewSourceName, OverviewSource>>;
}
