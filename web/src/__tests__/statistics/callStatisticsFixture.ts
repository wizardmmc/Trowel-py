/** 提供调用详情组件测试使用的去正文跨层 trace 样例。 */

import type {
  CallDetail,
  CallList,
} from "../../statistics/domain/types";

export const callListFixture: CallList = {
  generated_at: "2026-08-03T12:01:00Z",
  window_start: "2026-08-03T00:00:00Z",
  window_end: "2026-08-04T00:00:00Z",
  timezone: "UTC",
  sample_size: 2,
  quality: "partial",
  freshness: {
    telemetry: { updated_at: "2026-08-03T12:00:01Z", status: "fresh" },
  },
  items: [
    {
      trace_id: "00000000000000000000000000000002",
      span_id: "0000000000000002",
      started_at: "2026-08-03T12:00:01Z",
      duration_ms: 4600,
      status: "ok",
      component: "mcp",
      operation: "mcp.tools.call",
      runtime: "codex",
      quality: "partial",
    },
    {
      trace_id: "00000000000000000000000000000001",
      span_id: "0000000000000001",
      started_at: "2026-08-03T12:00:00Z",
      duration_ms: 18,
      status: "ok",
      component: "fastapi",
      operation: "http.agent.messages",
      runtime: null,
      quality: "reliable",
    },
  ],
  next_cursor: "next-page",
};

export const callDetailFixture: CallDetail = {
  generated_at: "2026-08-03T12:01:00Z",
  trace_id: "00000000000000000000000000000002",
  started_at: "2026-08-03T12:00:01Z",
  ended_at: "2026-08-03T12:00:05.6Z",
  root_operation: "mcp.tools.call",
  status: "ok",
  quality: "partial",
  sample_size: 3,
  freshness: callListFixture.freshness,
  spans: [
    {
      trace_id: "00000000000000000000000000000001",
      span_id: "0000000000000001",
      parent_span_id: null,
      links: [],
      component: "fastapi",
      operation: "http.agent.messages",
      started_at: "2026-08-03T12:00:00Z",
      ended_at: "2026-08-03T12:00:00.018Z",
      duration_ms: 18,
      status: "ok",
      runtime: null,
      attributes: {},
    },
    {
      trace_id: "00000000000000000000000000000001",
      span_id: "0000000000000003",
      parent_span_id: "0000000000000001",
      links: [],
      component: "sqlite",
      operation: "sqlite.sessions.read",
      started_at: "2026-08-03T12:00:00.004Z",
      ended_at: "2026-08-03T12:00:00.007Z",
      duration_ms: 3,
      status: "ok",
      runtime: null,
      attributes: { retry_count: 0 },
    },
    {
      trace_id: "00000000000000000000000000000002",
      span_id: "0000000000000002",
      parent_span_id: null,
      links: [
        {
          trace_id: "00000000000000000000000000000001",
          span_id: "0000000000000001",
          available: true,
        },
      ],
      component: "mcp",
      operation: "mcp.tools.call",
      started_at: "2026-08-03T12:00:01Z",
      ended_at: "2026-08-03T12:00:05.6Z",
      duration_ms: 4600,
      status: "ok",
      runtime: "codex",
      attributes: { retry_count: 2 },
    },
  ],
  unavailable: [
    {
      code: "native_runtime_black_box",
      reason: "原生 runtime 未传播调用上下文，内部调度区间未采集。",
      source_span_id: "0000000000000002",
      started_at: null,
      ended_at: null,
    },
  ],
};
