/** 纯 props 展示可筛选调用列表、真实父子层级、span link 和采集缺口。 */

import type { KeyboardEvent } from "react";
import {
  TELEMETRY_COMPONENTS,
  TELEMETRY_OPERATIONS,
} from "../../../shared/telemetry-contracts";
import type {
  CallComponent,
  CallDetail,
  CallFilters,
  CallList,
  CallListItem,
  CallSpan,
  CallStatus,
  StatisticsQuality,
} from "../domain/types";
import { StatisticsSelect } from "./StatisticsSelect";
import "./call-statistics.css";

export interface CallStatisticsPanelProps {
  readonly calls: CallList | null;
  readonly detail: CallDetail | null;
  readonly filters: CallFilters;
  readonly selectedSpanId: string | null;
  readonly loading: boolean;
  readonly error: string | null;
  readonly detailLoading: boolean;
  readonly detailError: string | null;
  readonly loadingMore: boolean;
  readonly onFiltersChange: (filters: CallFilters) => void;
  readonly onSelectCall: (call: CallListItem) => void;
  readonly onLoadMore: () => void;
}

const COMPONENT_LABELS: Readonly<Record<CallComponent, string>> = {
  electron: "Electron",
  renderer: "Renderer",
  sidecar: "Sidecar",
  fastapi: "FastAPI",
  agent_host: "Agent Host",
  runtime: "Runtime",
  mcp: "MCP",
  sqlite: "SQLite",
  telemetry: "Telemetry",
};

const COMPONENT_OPTIONS = [
  { value: "all", label: "全部组件" },
  ...TELEMETRY_COMPONENTS.map((component) => ({
    value: component,
    label: COMPONENT_LABELS[component],
  })),
] as const;

const STATUS_LABELS: Readonly<Record<CallStatus, string>> = {
  ok: "完成",
  error: "失败",
  unset: "未结束",
};

const RUNTIME_OPTIONS = [
  { value: "all", label: "全部 Runtime" },
  { value: "claude_code", label: "Claude Code" },
  { value: "codex", label: "Codex" },
] as const;

const STATUS_OPTIONS = [
  { value: "all", label: "全部状态" },
  { value: "ok", label: "完成" },
  { value: "error", label: "失败" },
  { value: "unset", label: "未结束" },
] as const;

const MINIMUM_DURATION_OPTIONS = [
  { value: "0", label: "不限" },
  { value: "100", label: "100 ms" },
  { value: "1000", label: "1 秒" },
  { value: "5000", label: "5 秒" },
] as const;

const QUALITY_LABELS: Readonly<Record<StatisticsQuality, string>> = {
  reliable: "链路完整",
  partial: "存在缺口",
  unavailable: "不可用",
};

/** 展示调用筛选、调用列表和当前选中调用的跨层轨迹。 */
export function CallStatisticsPanel({
  calls,
  detail,
  filters,
  selectedSpanId,
  loading,
  error,
  detailLoading,
  detailError,
  loadingMore,
  onFiltersChange,
  onSelectCall,
  onLoadMore,
}: CallStatisticsPanelProps) {
  const updateFilter = <Key extends keyof CallFilters>(
    key: Key,
    value: CallFilters[Key],
  ) => onFiltersChange({ ...filters, [key]: value });

  return (
    <section className="call-statistics" aria-label="调用详情">
      <div className="call-statistics__filters">
        <label>
          <span>调用组件</span>
          <StatisticsSelect
            ariaLabel="调用组件"
            value={filters.component}
            options={COMPONENT_OPTIONS}
            triggerClassName="call-statistics__filter-select"
            onValueChange={(value) =>
              updateFilter(
                "component",
                value as CallFilters["component"],
              )
            }
          />
        </label>
        <label>
          <span>操作</span>
          <StatisticsSelect
            ariaLabel="调用操作"
            value={filters.operation}
            options={[
              { value: "all", label: "全部操作" },
              ...TELEMETRY_OPERATIONS.map((operation) => ({
                value: operation,
                label: operation,
              })),
            ]}
            triggerClassName="call-statistics__filter-select"
            onValueChange={(value) =>
              updateFilter(
                "operation",
                value as CallFilters["operation"],
              )
            }
          />
        </label>
        <label>
          <span>Runtime</span>
          <StatisticsSelect
            ariaLabel="调用 Runtime"
            value={filters.runtime}
            options={RUNTIME_OPTIONS}
            triggerClassName="call-statistics__filter-select"
            onValueChange={(value) =>
              updateFilter(
                "runtime",
                value as CallFilters["runtime"],
              )
            }
          />
        </label>
        <label>
          <span>状态</span>
          <StatisticsSelect
            ariaLabel="调用状态"
            value={filters.status}
            options={STATUS_OPTIONS}
            triggerClassName="call-statistics__filter-select"
            onValueChange={(value) =>
              updateFilter(
                "status",
                value as CallFilters["status"],
              )
            }
          />
        </label>
        <label>
          <span>最小耗时</span>
          <StatisticsSelect
            ariaLabel="最小耗时"
            value={String(filters.minimumDurationMs)}
            options={MINIMUM_DURATION_OPTIONS}
            triggerClassName="call-statistics__filter-select"
            onValueChange={(value) =>
              updateFilter("minimumDurationMs", Number(value))
            }
          />
        </label>
      </div>

      {error && <div className="call-statistics__notice is-error">{error}</div>}
      <div className="call-statistics__layout">
        <CallListTable
          calls={calls}
          selectedSpanId={selectedSpanId}
          loading={loading}
          loadingMore={loadingMore}
          onSelectCall={onSelectCall}
          onLoadMore={onLoadMore}
        />
        <TraceDetail
          detail={detail}
          loading={detailLoading}
          error={detailError}
        />
      </div>
    </section>
  );
}

/** 展示可分页、可键盘选择的最近调用表。 */
function CallListTable({
  calls,
  selectedSpanId,
  loading,
  loadingMore,
  onSelectCall,
  onLoadMore,
}: {
  readonly calls: CallList | null;
  readonly selectedSpanId: string | null;
  readonly loading: boolean;
  readonly loadingMore: boolean;
  readonly onSelectCall: (call: CallListItem) => void;
  readonly onLoadMore: () => void;
}) {
  const items = calls?.items ?? [];
  const onRowKeyDown = (
    event: KeyboardEvent<HTMLTableRowElement>,
    index: number,
  ) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onSelectCall(items[index]);
      return;
    }
    const offset =
      event.key === "ArrowDown" ? 1 : event.key === "ArrowUp" ? -1 : 0;
    if (offset === 0) return;
    event.preventDefault();
    const nextIndex = Math.max(0, Math.min(items.length - 1, index + offset));
    onSelectCall(items[nextIndex]);
    const rows = event.currentTarget.parentElement?.querySelectorAll("tr");
    rows?.item(nextIndex).focus();
  };

  return (
    <section className="call-statistics__surface call-statistics__list">
      <header>
        <strong>最近调用</strong>
        <span>{loading ? "正在刷新…" : `${items.length} 条 · 已去正文`}</span>
      </header>
      <div className="call-statistics__table-scroll">
        <table role="grid" aria-label="最近调用">
          <thead>
            <tr>
              <th>时间</th>
              <th>调用</th>
              <th>耗时</th>
              <th>状态</th>
              <th>层</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item, index) => (
              <tr
                key={item.span_id}
                tabIndex={
                  item.span_id === selectedSpanId ||
                  (selectedSpanId === null && index === 0)
                    ? 0
                    : -1
                }
                aria-selected={item.span_id === selectedSpanId}
                onClick={() => onSelectCall(item)}
                onKeyDown={(event) => onRowKeyDown(event, index)}
              >
                <td className="is-mono">{formatTime(item.started_at)}</td>
                <td>
                  <strong>{item.operation}</strong>
                  <small>{shortId(item.trace_id)}</small>
                </td>
                <td className="is-mono">{formatDuration(item.duration_ms)}</td>
                <td>
                  <span className={`call-statistics__status is-${item.status}`}>
                    {STATUS_LABELS[item.status]}
                  </span>
                </td>
                <td>{componentLabel(item.component)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!loading && items.length === 0 && (
        <div className="call-statistics__empty">当前筛选范围没有调用</div>
      )}
      {calls?.next_cursor && (
        <button
          className="call-statistics__more"
          type="button"
          disabled={loadingMore}
          onClick={onLoadMore}
        >
          {loadingMore ? "正在读取…" : "加载更早调用"}
        </button>
      )}
    </section>
  );
}

/** 展示选中调用的 span、link、黑盒区间和隐私说明。 */
function TraceDetail({
  detail,
  loading,
  error,
}: {
  readonly detail: CallDetail | null;
  readonly loading: boolean;
  readonly error: string | null;
}) {
  if (error) {
    return (
      <aside className="call-statistics__surface call-statistics__empty is-error">
        {error}
      </aside>
    );
  }
  if (loading && !detail) {
    return (
      <aside className="call-statistics__surface call-statistics__empty">
        正在读取调用链…
      </aside>
    );
  }
  if (!detail) {
    return (
      <aside className="call-statistics__surface call-statistics__empty">
        选择一条调用查看实际采集链路
      </aside>
    );
  }

  const depths = spanDepths(detail.spans);
  return (
    <aside
      className="call-statistics__surface call-statistics__detail"
      aria-live="polite"
    >
      <header>
        <strong>调用链</strong>
        <span>
          {shortId(detail.trace_id)} · {QUALITY_LABELS[detail.quality]}
        </span>
      </header>
      <div className="call-statistics__trace-summary">
        <div>
          <span>根操作</span>
          <strong>{detail.root_operation}</strong>
        </div>
        <div>
          <span>总区间</span>
          <strong>
            {formatDuration(
              new Date(detail.ended_at).getTime() -
                new Date(detail.started_at).getTime(),
            )}
          </strong>
        </div>
        <div>
          <span>已采集</span>
          <strong>{detail.sample_size} 个 span</strong>
        </div>
      </div>
      <div className="call-statistics__trace" aria-label="调用轨迹">
        {detail.spans.map((span) => (
          <TraceSpanRow
            key={`${span.trace_id}:${span.span_id}`}
            span={span}
            depth={depths.get(`${span.trace_id}:${span.span_id}`) ?? 0}
          />
        ))}
      </div>
      {detail.unavailable.length > 0 && (
        <section className="call-statistics__gaps" aria-label="未采集区间">
          <strong>未采集区间</strong>
          {detail.unavailable.map((gap, index) => (
            <p key={`${gap.code}:${gap.source_span_id ?? index}`}>
              {gap.reason}
            </p>
          ))}
        </section>
      )}
      <p className="call-statistics__privacy">
        只展示受控操作名和数值指标；不保存
        prompt、thinking、工具参数、正文或本机路径。
      </p>
    </aside>
  );
}

/** 展示一个 span 的层级、运行时、关联目标和耗时。 */
function TraceSpanRow({
  span,
  depth,
}: {
  readonly span: CallSpan;
  readonly depth: number;
}) {
  return (
    <div className="call-statistics__trace-row">
      <span className="call-statistics__trace-layer">
        {componentLabel(span.component)}
      </span>
      <div
        className="call-statistics__trace-name"
        style={{ paddingLeft: `${14 + depth * 18}px` }}
      >
        <strong data-depth={depth}>{span.operation}</strong>
        <small>
          {span.runtime ? `${span.runtime} · ` : ""}
          {shortId(span.span_id)}
        </small>
        {span.links.map((link) => (
          <span
            className="call-statistics__link"
            key={`${link.trace_id}:${link.span_id}`}
          >
            关联到 {shortId(link.trace_id)}
            {link.available ? "" : "（目标未采集）"}
          </span>
        ))}
      </div>
      <span className={`call-statistics__trace-duration is-${span.status}`}>
        {formatDuration(span.duration_ms)}
      </span>
    </div>
  );
}

/** 按同一 trace 内经校验的 parent_span_id 计算每个 span 的展示深度。 */
function spanDepths(spans: readonly CallSpan[]): ReadonlyMap<string, number> {
  const byKey = new Map(
    spans.map((span) => [`${span.trace_id}:${span.span_id}`, span]),
  );
  const result = new Map<string, number>();
  const resolve = (span: CallSpan, path: Set<string>): number => {
    const key = `${span.trace_id}:${span.span_id}`;
    const cached = result.get(key);
    if (cached !== undefined) return cached;
    if (!span.parent_span_id || path.has(key)) return 0;
    const parent = byKey.get(`${span.trace_id}:${span.parent_span_id}`);
    if (!parent) return 0;
    const nextPath = new Set(path);
    nextPath.add(key);
    const depth = resolve(parent, nextPath) + 1;
    result.set(key, depth);
    return depth;
  };
  spans.forEach((span) =>
    result.set(`${span.trace_id}:${span.span_id}`, resolve(span, new Set())),
  );
  return result;
}

/** 返回受控组件名的界面标签。 */
function componentLabel(component: CallComponent): string {
  return COMPONENT_LABELS[component];
}

/** 把随机身份缩短为仅供辨认的八位前缀。 */
function shortId(value: string): string {
  return value.slice(0, 8);
}

/** 按中文 24 小时制显示调用开始时刻。 */
function formatTime(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date(value));
}

/** 按数量级把毫秒耗时显示为毫秒、秒或分秒。 */
function formatDuration(milliseconds: number): string {
  if (!Number.isFinite(milliseconds)) return "—";
  if (milliseconds < 1000)
    return `${milliseconds.toFixed(milliseconds < 10 ? 1 : 0)} ms`;
  if (milliseconds < 60_000) return `${(milliseconds / 1000).toFixed(2)} 秒`;
  return `${Math.floor(milliseconds / 60_000)} 分 ${Math.round((milliseconds % 60_000) / 1000)} 秒`;
}
