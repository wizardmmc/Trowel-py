/** 定义 Electron、renderer 与 Python sidecar 之间的遥测批次契约。 */

export type TelemetryComponent =
  | "electron"
  | "renderer"
  | "sidecar"
  | "fastapi"
  | "agent_host"
  | "runtime"
  | "mcp"
  | "sqlite"
  | "telemetry";

export type TelemetryOperation =
  | "desktop.start"
  | "desktop.start.sidecar_ready"
  | "desktop.start.first_screen"
  | "desktop.window.close"
  | "desktop.renderer.crash"
  | "desktop.exit"
  | "desktop.reconcile"
  | "sidecar.sample"
  | "sidecar.exit"
  | "renderer.measure"
  | "http.agent.messages"
  | "http.statistics.query"
  | "sse.connect"
  | "sse.first_event"
  | "sse.disconnect"
  | "sse.reconnect"
  | "sse.close"
  | "agent.turn"
  | "agent.interrupt"
  | "runtime.call"
  | "runtime.tool"
  | "mcp.tools.call"
  | "sqlite.query"
  | "sqlite.transaction"
  | "sqlite.sessions.read"
  | "sqlite.sessions.write"
  | "sqlite.workspaces.read"
  | "sqlite.workspaces.write"
  | "resource.app.close"
  | "resource.runtime_connection.close"
  | "resource.session.close"
  | "resource.turn.close"
  | "telemetry.collect"
  | "telemetry.flush"
  | "telemetry.aggregate"
  | "telemetry.cleanup";

/** runtime 实际回报的模型名；具体型号不属于前后端静态契约。 */
export type TelemetryModel = string;

export type TelemetryMetricName =
  | "telemetry.accepted"
  | "telemetry.rejected"
  | "telemetry.dropped"
  | "telemetry.database_bytes"
  | "desktop.exit_terminal"
  | "desktop.remaining_resources"
  | "sidecar.uptime_ms"
  | "sidecar.rss_bytes"
  | "sidecar.restart"
  | "sidecar.abnormal_exit"
  | "sse.disconnect"
  | "sse.reconnect"
  | "sqlite.busy"
  | "sqlite.locked"
  | "resource.remaining";

export interface TelemetryAttributes {
  readonly quality?: "reliable" | "partial" | "unavailable";
  readonly sampled?: boolean;
  readonly retry_count?: number;
  readonly row_count_bucket?: "0" | "1-10" | "11-100" | "101-1000" | "1000+";
  readonly transport?: "http" | "sse" | "stdio" | "ipc" | "sqlite";
  readonly error_category?:
    | "timeout"
    | "busy"
    | "locked"
    | "validation"
    | "unavailable"
    | "cancelled"
    | "crash"
    | "unknown";
  readonly black_box?: boolean;
  readonly exit_mode?: "cooperative" | "forced";
  readonly process_tree_result?: "closed" | "needs_reconcile";
}

export interface TelemetryTraceLink {
  readonly trace_id: string;
  readonly span_id: string | null;
}

export interface TelemetrySpan {
  readonly trace_id: string;
  readonly span_id: string;
  readonly parent_span_id: string | null;
  readonly started_at: string;
  readonly ended_at: string;
  readonly component: TelemetryComponent;
  readonly operation: TelemetryOperation;
  readonly status: "ok" | "error" | "unset";
  readonly runtime: "claude_code" | "codex" | null;
  readonly model: TelemetryModel | null;
  readonly session_ref: string | null;
  readonly call_ref: string | null;
  readonly attributes: TelemetryAttributes;
  readonly links: readonly TelemetryTraceLink[];
}

export interface TelemetryMetric {
  readonly metric_id: string;
  readonly observed_at: string;
  readonly component: TelemetryComponent;
  readonly name: TelemetryMetricName;
  readonly kind: "counter" | "gauge" | "histogram";
  readonly unit: "1" | "By" | "ms";
  readonly value: number;
  readonly status: "ok" | "error" | "unset";
  readonly runtime: "claude_code" | "codex" | null;
  readonly model: TelemetryModel | null;
  readonly operation: TelemetryOperation | null;
  readonly attributes: TelemetryAttributes;
}

export interface TelemetryBatch {
  readonly batch_id: string;
  readonly schema_version: 1;
  readonly source_component: TelemetryComponent;
  readonly collected_at: string;
  readonly mode: "normal" | "backfill";
  readonly spans: readonly TelemetrySpan[];
  readonly metrics: readonly TelemetryMetric[];
}

export interface TelemetrySubmitData {
  readonly accepted: number;
  readonly rejected: number;
  readonly dropped: number;
  readonly duplicate: boolean;
  readonly error_categories: Readonly<Record<string, number>>;
}
