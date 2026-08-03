/** 提供运行统计组件测试使用的完整 API 形状。 */

import type {
  RuntimeDistribution,
  RuntimeStatistics,
} from "../../statistics/domain/types";

function distribution(
  operation: string,
  label: string,
  sampleSize = 20,
): RuntimeDistribution {
  return {
    operation,
    label,
    sample_size: sampleSize,
    error_count: 0,
    p50_ms: sampleSize >= 5 ? 50 : null,
    p95_ms: sampleSize >= 20 ? 100 : null,
    p99_ms: null,
    quality: sampleSize >= 20 ? "reliable" : "partial",
  };
}

export const runtimeStatisticsFixture: RuntimeStatistics = {
  generated_at: "2026-08-03T12:01:00Z",
  window_start: "2026-08-03T00:00:00Z",
  window_end: "2026-08-04T00:00:00Z",
  timezone: "UTC",
  sample_size: 84,
  quality: "reliable",
  freshness: {
    telemetry: { updated_at: "2026-08-03T12:00:00Z", status: "fresh" },
  },
  resolution: "hour",
  sidecar: {
    uptime: {
      value: 3_240_000,
      unit: "ms",
      observed_at: "2026-08-03T12:00:00Z",
      sample_size: 108,
      quality: "reliable",
    },
    rss: {
      value: 1_084_227_584,
      unit: "By",
      observed_at: "2026-08-03T12:00:00Z",
      sample_size: 1,
      quality: "partial",
    },
    restart_count: 1,
    abnormal_exit_count: 1,
    rss_series: [
      {
        bucket_start: "2026-08-03T12:00:00Z",
        minimum: 1_084_227_584,
        maximum: 1_084_227_584,
        average: 1_084_227_584,
        sample_size: 1,
      },
    ],
  },
  last_clean_exit_at: "2026-08-03T10:30:00Z",
  lifecycle: [
    distribution("desktop.start.sidecar_ready", "启动 → Sidecar 就绪"),
    distribution("desktop.start.first_screen", "启动 → 首屏可用", 4),
    distribution("resource.session.close", "Session 关闭"),
    distribution("desktop.exit", "应用退出 → 进程树终态"),
    distribution("desktop.reconcile", "崩溃残留恢复"),
  ],
  fastapi: [
    distribution("http.agent.messages", "Agent 消息路由"),
    distribution("http.statistics.query", "统计查询路由"),
  ],
  sse: {
    connect_count: 8,
    disconnect_count: 2,
    reconnect_count: 1,
    operations: [
      distribution("sse.connect", "SSE 建连"),
      distribution("sse.first_event", "SSE 首事件"),
      distribution("sse.disconnect", "SSE 断线", 2),
      distribution("sse.reconnect", "SSE 重连", 1),
      distribution("sse.close", "SSE 关闭"),
    ],
    quality: "reliable",
  },
  sqlite: {
    busy_count: 2,
    locked_count: 1,
    operations: [
      distribution("sqlite.sessions.read", "Sessions 读取"),
      distribution("sqlite.sessions.write", "Sessions 写入"),
      distribution("sqlite.workspaces.read", "Workspaces 读取"),
      distribution("sqlite.workspaces.write", "Workspaces 写入"),
    ],
    files: [
      {
        name: "sessions.db",
        owner: "memory.sessions",
        database_bytes: 2_711_552,
        wal_bytes: 4096,
        shm_bytes: 0,
        total_bytes: 2_715_648,
        quality: "reliable",
      },
      {
        name: "workspaces.db",
        owner: "agent.workspaces",
        database_bytes: 16_384,
        wal_bytes: 0,
        shm_bytes: 0,
        total_bytes: 16_384,
        quality: "reliable",
      },
      {
        name: "telemetry.db",
        owner: "telemetry",
        database_bytes: 65_536,
        wal_bytes: 8192,
        shm_bytes: 32_768,
        total_bytes: 106_496,
        quality: "reliable",
      },
    ],
    quality: "reliable",
  },
  resources: [
    distribution("resource.app.close", "App owner"),
    distribution("resource.runtime_connection.close", "Runtime connection owner"),
    distribution("resource.session.close", "Session owner"),
    distribution("resource.turn.close", "Turn owner"),
  ],
  resource_remaining_count: 1,
  gaps: [
    {
      code: "rss_single_sample",
      message: "RSS 只有一次采样，只展示事实，不判断上涨。",
    },
  ],
};
