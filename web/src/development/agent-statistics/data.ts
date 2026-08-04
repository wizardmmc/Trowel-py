/** 提供不含真实标识符的双 runtime Agent 统计预览数据。 */

import type {
  AgentLatencyDistribution,
  AgentStatistics,
  AgentTokenUsage,
} from "../../statistics/domain/types";

const CLAUDE_ACTIVITY_MS = 11_271;
const CODEX_ACTIVITY_MS = 80_000;

const CLAUDE_TOKENS = {
  input: 14_621,
  output: 1_290,
  cache_read: 13_300,
  cache_creation: 500,
  reasoning: null,
  unknown: null,
  total: 15_911,
  total_includes_cache_input: true,
  known_session_count: 1,
  session_count: 1,
  quality: "reliable",
} satisfies AgentTokenUsage;

const CODEX_TOKENS = {
  input: 240_880,
  output: 1_959,
  cache_read: 193_495,
  cache_creation: null,
  reasoning: null,
  unknown: null,
  total: 242_839,
  total_includes_cache_input: true,
  known_session_count: 1,
  session_count: 1,
  quality: "reliable",
} satisfies AgentTokenUsage;

const NO_LATENCY_SAMPLE = {
  sample_size: 0,
  p50_ms: null,
  p95_ms: null,
  p99_ms: null,
  quality: "unavailable",
} satisfies AgentLatencyDistribution;

const ONE_LATENCY_SAMPLE = {
  ...NO_LATENCY_SAMPLE,
  sample_size: 1,
} satisfies AgentLatencyDistribution;

/** 总 token、状态和展示比例取自脱敏实测，标识和时间已经替换。 */
export const agentStatisticsPreviewData: AgentStatistics = {
  generated_at: "2026-08-03T04:00:00Z",
  window_start: "2026-08-03T00:00:00+08:00",
  window_end: "2026-08-04T00:00:00+08:00",
  timezone: "Asia/Shanghai",
  sample_size: 2,
  quality: "reliable",
  freshness: {
    agent_sessions: {
      updated_at: "2026-08-03T03:00:00Z",
      status: "fresh",
    },
  },
  statuses: {
    completed: 1,
    running: 0,
    interrupted: 1,
    failed: 0,
    unknown: 0,
  },
  tokens: {
    input: 255_501,
    output: 3_249,
    cache_read: 206_795,
    cache_creation: 500,
    reasoning: null,
    unknown: null,
    total: 258_750,
    total_includes_cache_input: true,
    known_session_count: 2,
    session_count: 2,
    quality: "reliable",
  },
  first_visible_response: {
    sample_size: 1,
    p50_ms: null,
    p95_ms: null,
    p99_ms: null,
    quality: "unavailable",
  },
  activity: {
    session_sum_ms: CLAUDE_ACTIVITY_MS + CODEX_ACTIVITY_MS,
    concurrent_union_ms: CLAUDE_ACTIVITY_MS + CODEX_ACTIVITY_MS,
    quality: "reliable",
  },
  cache_input_ratio:
    (CLAUDE_TOKENS.cache_read + CODEX_TOKENS.cache_read) /
    (CLAUDE_TOKENS.input +
      CLAUDE_TOKENS.cache_read +
      CLAUDE_TOKENS.cache_creation +
      CODEX_TOKENS.input),
  model_summaries: [
    {
      runtime: "claude_code",
      model: "glm-5.2",
      session_count: 1,
      statuses: {
        completed: 0,
        running: 0,
        interrupted: 1,
        failed: 0,
        unknown: 0,
      },
      tokens: CLAUDE_TOKENS,
      first_visible_response: NO_LATENCY_SAMPLE,
      cache_input_ratio:
        CLAUDE_TOKENS.cache_read /
        (CLAUDE_TOKENS.input +
          CLAUDE_TOKENS.cache_read +
          CLAUDE_TOKENS.cache_creation),
      activity_ms: CLAUDE_ACTIVITY_MS,
      quality: "reliable",
    },
    {
      runtime: "codex",
      model: "gpt-5.6-sol",
      session_count: 1,
      statuses: {
        completed: 1,
        running: 0,
        interrupted: 0,
        failed: 0,
        unknown: 0,
      },
      tokens: CODEX_TOKENS,
      first_visible_response: ONE_LATENCY_SAMPLE,
      cache_input_ratio: 0.805,
      activity_ms: CODEX_ACTIVITY_MS,
      quality: "reliable",
    },
  ],
  sessions: [
    {
      session_id: "preview-cc-interrupt",
      runtime: "claude_code",
      models: ["glm-5.2"],
      started_at: "2026-08-03T02:38:00Z",
      activity_ms: CLAUDE_ACTIVITY_MS,
      first_visible_response: NO_LATENCY_SAMPLE,
      tokens: CLAUDE_TOKENS,
      status: "interrupted",
      quality: "reliable",
    },
    {
      session_id: "preview-codex-session",
      runtime: "codex",
      models: ["gpt-5.6-sol"],
      started_at: "2026-08-03T01:18:00Z",
      activity_ms: CODEX_ACTIVITY_MS,
      first_visible_response: ONE_LATENCY_SAMPLE,
      tokens: CODEX_TOKENS,
      status: "completed",
      quality: "reliable",
    },
  ],
};
