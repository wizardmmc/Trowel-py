/** 纯 props 展示 Agent session、模型、token、首响和数据质量。 */

import type {
  AgentLatencyDistribution,
  AgentModelSummary,
  AgentRuntime,
  AgentRuntimeFilter,
  AgentSession,
  AgentSessionStatus,
  AgentStatistics,
  AgentStatusCounts,
  StatisticsQuality,
} from "../domain/types";
import { StatisticsSelect } from "./StatisticsSelect";
import "./agent-statistics.css";

export interface AgentStatisticsPanelProps {
  readonly data: AgentStatistics | null;
  readonly loading: boolean;
  readonly error: string | null;
  readonly runtimeFilter: AgentRuntimeFilter;
  readonly modelFilter: string;
  readonly onRuntimeFilterChange: (runtime: AgentRuntimeFilter) => void;
  readonly onModelFilterChange: (model: string) => void;
}

const STATUS_PRESENTATION: Readonly<
  Record<AgentSessionStatus, { label: string; tone: string }>
> = {
  completed: { label: "完成", tone: "success" },
  running: { label: "运行中", tone: "warning" },
  interrupted: { label: "已中断", tone: "error" },
  failed: { label: "失败", tone: "error" },
  unknown: { label: "未知", tone: "muted" },
};

const QUALITY_LABELS: Readonly<Record<StatisticsQuality, string>> = {
  reliable: "数据完整",
  partial: "部分数据",
  unavailable: "数据不可用",
};

const RUNTIME_FILTER_OPTIONS = [
  { value: "all", label: "全部 Runtime" },
  { value: "claude_code", label: "Claude Code" },
  { value: "codex", label: "Codex" },
] as const;

export function AgentStatisticsPanel({
  data,
  loading,
  error,
  runtimeFilter,
  modelFilter,
  onRuntimeFilterChange,
  onModelFilterChange,
}: AgentStatisticsPanelProps) {
  const modelOptions = Array.from(
    new Set(
      (data?.model_summaries ?? [])
        .filter((item) => runtimeFilter === "all" || item.runtime === runtimeFilter)
        .map((item) => item.model)
        .filter((model): model is string => model !== null),
    ),
  ).sort();
  const modelRows = (data?.model_summaries ?? []).filter(
    (item) =>
      (runtimeFilter === "all" || item.runtime === runtimeFilter) &&
      (modelFilter === "all" || item.model === modelFilter),
  );
  const sessionRows = (data?.sessions ?? []).filter(
    (item) =>
      (runtimeFilter === "all" || item.runtime === runtimeFilter) &&
      (modelFilter === "all" || item.models.includes(modelFilter)),
  );

  return (
    <section className="agent-statistics" aria-label="Agent 统计">
      {error && <div className="agent-statistics__notice is-error">{error}</div>}
      {loading && !data && (
        <div className="agent-statistics__notice">正在读取 Agent 统计…</div>
      )}
      {!loading && !data && (
        <div className="agent-statistics__empty">
          <strong>Agent 统计数据不可用</strong>
          <span>当前时间窗没有可读取的 session 来源</span>
        </div>
      )}

      {data && (
        <>
          <FactStrip data={data} />
          <div className="agent-statistics__two-column">
            <StatusPanel statuses={data.statuses} total={data.sample_size} />
            <UsagePanel data={data} />
          </div>
          <div className="agent-statistics__filters">
            <strong>明细筛选</strong>
            <div className="agent-statistics__filter-group">
              <label>
                <span>Runtime</span>
                <StatisticsSelect
                  ariaLabel="Runtime 筛选"
                  value={runtimeFilter}
                  options={RUNTIME_FILTER_OPTIONS}
                  triggerClassName="agent-statistics__filter-select"
                  onValueChange={(value) =>
                    onRuntimeFilterChange(
                      value as AgentRuntimeFilter,
                    )
                  }
                />
              </label>
              <label>
                <span>模型</span>
                <StatisticsSelect
                  ariaLabel="模型筛选"
                  value={modelFilter}
                  options={[
                    { value: "all", label: "全部模型" },
                    ...modelOptions.map((model) => ({
                      value: model,
                      label: model,
                    })),
                  ]}
                  triggerClassName="agent-statistics__filter-select"
                  onValueChange={onModelFilterChange}
                />
              </label>
            </div>
            <QualityBadge quality={data.quality} />
          </div>
          <ModelTable rows={modelRows} />
          <SessionTable rows={sessionRows} timezone={data.timezone} />
        </>
      )}
    </section>
  );
}

function FactStrip({ data }: { readonly data: AgentStatistics }) {
  return (
    <div className="agent-statistics__facts">
      <Fact label="用户 SESSION" value={String(data.sample_size)} meta="Trowel 会话口径" />
      <Fact
        label="SESSION 状态"
        value={`${data.statuses.completed} / ${data.sample_size}`}
        meta={`${data.statuses.running} 运行 · ${data.statuses.interrupted} 中断 · ${data.statuses.failed} 失败 · ${data.statuses.unknown} 未知`}
      />
      <Fact
        label="合计 TOKEN"
        value={formatTokens(data.tokens.total)}
        meta={`${data.tokens.total_includes_cache_input ? "包含缓存输入" : "不含缓存输入"} · ${tokenCoverage(data.tokens.known_session_count, data.tokens.session_count)}`}
      />
      <Fact
        label="首次可见响应"
        value={formatLatency(data.first_visible_response.p50_ms)}
        meta={`p50 · p95 ${formatLatency(data.first_visible_response.p95_ms)} · n=${data.first_visible_response.sample_size}`}
      />
    </div>
  );
}

function Fact({
  label,
  value,
  meta,
}: {
  readonly label: string;
  readonly value: string;
  readonly meta: string;
}) {
  return (
    <div className="agent-statistics__fact">
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{meta}</small>
    </div>
  );
}

function StatusPanel({
  statuses,
  total,
}: {
  readonly statuses: AgentStatusCounts;
  readonly total: number;
}) {
  const rows: readonly [AgentSessionStatus, number][] = [
    ["completed", statuses.completed],
    ["running", statuses.running],
    ["interrupted", statuses.interrupted],
    ["failed", statuses.failed],
    ["unknown", statuses.unknown],
  ];
  return (
    <section className="agent-statistics__surface">
      <header><strong>Session 状态</strong><span>未知=缺少可靠终态，不代表运行中</span></header>
      <div className="agent-statistics__metrics">
        {rows.map(([status, count]) => (
          <div className="agent-statistics__metric" key={status}>
            <span className="agent-statistics__metric-name">
              {STATUS_PRESENTATION[status].label}
            </span>
            <span className="agent-statistics__meter" aria-hidden="true">
              <i
                className={`is-${STATUS_PRESENTATION[status].tone}`}
                style={{ width: `${total ? (count / total) * 100 : 0}%` }}
              />
            </span>
            <strong>{count} / {total}</strong>
          </div>
        ))}
      </div>
    </section>
  );
}

function UsagePanel({ data }: { readonly data: AgentStatistics }) {
  return (
    <section className="agent-statistics__surface">
      <header><strong>使用概况</strong><span>当前时间窗</span></header>
      <dl className="agent-statistics__usage-grid">
        <UsageFact label="Session 时长之和" value={formatDuration(data.activity.session_sum_ms)} />
        <UsageFact label="并发活动并集" value={formatDuration(data.activity.concurrent_union_ms)} />
        <UsageFact
          label="缓存输入占比"
          value={formatRatio(data.cache_input_ratio)}
        />
        <UsageFact label="输出 token" value={formatTokens(data.tokens.output)} />
      </dl>
    </section>
  );
}

function UsageFact({ label, value }: { readonly label: string; readonly value: string }) {
  return <div><dt>{label}</dt><dd>{value}</dd></div>;
}

function ModelTable({ rows }: { readonly rows: readonly AgentModelSummary[] }) {
  return (
    <section className="agent-statistics__surface agent-statistics__table-section is-model-table">
      <header><strong>按模型汇总</strong><span>{rows.length} 个 runtime/model 分组</span></header>
      <div className="agent-statistics__table-scroll">
        <table>
          <thead><tr><th>模型</th><th>Runtime</th><th>Session</th><th>首响 p50 / p95</th><th>合计 Token</th><th>平均 / Session</th><th>缓存输入</th><th>状态</th><th>质量</th></tr></thead>
          <tbody>
            {rows.map((row) => (
              <tr key={`${row.runtime}:${row.model ?? "unknown"}`}>
                <td className="is-mono">{row.model ?? "未知模型"}</td>
                <td>{runtimeLabel(row.runtime)}</td>
                <td className="is-mono">{row.session_count}</td>
                <td className="is-mono">{formatDistribution(row.first_visible_response)}</td>
                <td className="is-mono">{formatTokens(row.tokens.total)}<small>{tokenCoverage(row.tokens.known_session_count, row.tokens.session_count)}</small></td>
                <td className="is-mono">{formatPerSession(row.tokens.total, row.tokens.known_session_count)}</td>
                <td className="is-mono">{formatRatio(row.cache_input_ratio)}</td>
                <td>{statusSummary(row.statuses)}</td>
                <td><QualityBadge quality={row.quality} /></td>
              </tr>
            ))}
            {rows.length === 0 && <tr><td colSpan={9} className="is-empty">当前筛选没有模型数据</td></tr>}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function SessionTable({
  rows,
  timezone,
}: {
  readonly rows: readonly AgentSession[];
  readonly timezone: string;
}) {
  return (
    <section className="agent-statistics__surface agent-statistics__table-section">
      <header><strong>最近 Session</strong><span>一行一个 Trowel session</span></header>
      <div className="agent-statistics__table-scroll">
        <table>
          <thead><tr><th>Session</th><th>Runtime / 模型</th><th>开始</th><th>会话内运行</th><th>Token</th><th>状态</th><th>质量</th></tr></thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.session_id}>
                <td className="is-mono" title={row.session_id}>
                  {formatSessionId(row.session_id)}
                </td>
                <td><span>{runtimeLabel(row.runtime)}</span><small>{row.models.join(" · ") || "未知模型"}</small></td>
                <td className="is-mono">{formatStart(row.started_at, timezone)}</td>
                <td>{formatDuration(row.activity_ms)}</td>
                <td className="is-mono">{formatTokens(row.tokens.total)}<small>{tokenCoverage(row.tokens.known_session_count, row.tokens.session_count)}</small></td>
                <td><StatusLabel status={row.status} /></td>
                <td><QualityBadge quality={row.quality} /></td>
              </tr>
            ))}
            {rows.length === 0 && <tr><td colSpan={7} className="is-empty">当前筛选没有 session</td></tr>}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function StatusLabel({ status }: { readonly status: AgentSessionStatus }) {
  const presentation = STATUS_PRESENTATION[status];
  return <span className={`agent-statistics__status is-${presentation.tone}`}>{presentation.label}</span>;
}

function QualityBadge({ quality }: { readonly quality: StatisticsQuality }) {
  return <span className={`agent-statistics__quality is-${quality}`}>{QUALITY_LABELS[quality]}</span>;
}

function runtimeLabel(runtime: AgentRuntime): string {
  return runtime === "codex" ? "Codex" : "Claude Code";
}

function formatTokens(value: number | null): string {
  if (value === null) return "不可用";
  return new Intl.NumberFormat("zh-CN", {
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(value);
}

function formatLatency(value: number | null): string {
  if (value === null) return "样本不足";
  if (value < 1_000) return `${Math.round(value)} 毫秒`;
  return `${(value / 1_000).toFixed(1)} 秒`;
}

function formatDuration(value: number): string {
  if (value < 60_000) return `${Math.round(value / 1_000)} 秒`;
  const minutes = Math.round(value / 60_000);
  if (minutes < 60) return `${minutes} 分钟`;
  const hours = Math.floor(minutes / 60);
  const remainder = minutes % 60;
  return remainder ? `${hours} 小时 ${remainder} 分` : `${hours} 小时`;
}

function formatRatio(value: number | null): string {
  return value === null ? "不可用" : `${(value * 100).toFixed(1)}%`;
}

function formatDistribution(value: AgentLatencyDistribution): string {
  return `${formatLatency(value.p50_ms)} / ${formatLatency(value.p95_ms)} · n=${value.sample_size}`;
}

function formatPerSession(total: number | null, sessions: number): string {
  return total === null || sessions === 0 ? "不可用" : formatTokens(total / sessions);
}

function tokenCoverage(known: number, total: number): string {
  if (total === 0) return "无 session 样本";
  return known === total
    ? `${known} / ${total} session 有水位`
    : `已知小计 · ${known} / ${total} session 有水位`;
}

function formatStart(value: string, timezone: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "不可用";
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: timezone,
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(parsed);
}

function statusSummary(statuses: AgentStatusCounts): string {
  const parts = [
    [statuses.completed, "完成"],
    [statuses.running, "运行"],
    [statuses.interrupted, "中断"],
    [statuses.failed, "失败"],
    [statuses.unknown, "未知"],
  ] as const;
  return (
    parts
      .filter(([count]) => count > 0)
      .map(([count, label]) => `${count} ${label}`)
      .join(" · ") || "无状态"
  );
}

function formatSessionId(value: string): string {
  return /^[0-9a-f]{16,}$/i.test(value) ? value.slice(0, 8) : value;
}
