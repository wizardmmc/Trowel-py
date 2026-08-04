/** 纯 props 展示桌面运行摘要、RSS 时序、生命周期、资源和数据库文件。 */

import type {
  DatabaseFileStatistics,
  RuntimeDistribution,
  RuntimeGaugePoint,
  RuntimeStatistics,
  StatisticsQuality,
} from "../domain/types";
import "./runtime-statistics.css";

export interface RuntimeStatisticsPanelProps {
  readonly data: RuntimeStatistics | null;
  readonly loading: boolean;
  readonly error: string | null;
}

const QUALITY_LABELS: Readonly<Record<StatisticsQuality, string>> = {
  reliable: "数据完整",
  partial: "样本不足",
  unavailable: "无数据",
};

const DATABASE_OWNER_LABELS: Readonly<Record<string, string>> = {
  "memory.sessions": "Memory 会话仓储",
  "agent.workspaces": "Agent 工作区仓储",
  telemetry: "运行遥测",
  unavailable: "待接入",
};

export function RuntimeStatisticsPanel({
  data,
  loading,
  error,
}: RuntimeStatisticsPanelProps) {
  return (
    <section className="runtime-statistics" aria-label="运行统计">
      {error && <div className="runtime-statistics__notice is-error">{error}</div>}
      {loading && !data && (
        <div className="runtime-statistics__notice">正在读取运行统计…</div>
      )}
      {!loading && !data && (
        <div className="runtime-statistics__empty">
          <strong>运行统计数据不可用</strong>
          <span>当前时间窗没有可读取的运行事实</span>
        </div>
      )}

      {data && (
        <>
          <Summary data={data} />
          {data.gaps.length > 0 && (
            <div className="runtime-statistics__gaps" aria-label="数据缺口">
              {data.gaps.map((gap) => (
                <span key={gap.code}>{gap.message}</span>
              ))}
            </div>
          )}
          <RssTimeline
            points={data.sidecar.rss_series}
            resolution={data.resolution}
            timezone={data.timezone}
          />
          <div className="runtime-statistics__two-column">
            <DistributionTable
              title="应用生命周期"
              note="分位数旁始终保留样本量"
              rows={data.lifecycle}
            />
            <SignalsTable data={data} />
          </div>
          <DistributionTable
            title="资源 owner"
            note={`最近一次核验残留 ${formatInteger(data.resource_remaining_count)}`}
            rows={data.resources}
          />
          <DatabaseFiles files={data.sqlite.files} />
        </>
      )}
    </section>
  );
}

function RssTimeline({
  points,
  resolution,
  timezone,
}: {
  readonly points: readonly RuntimeGaugePoint[];
  readonly resolution: RuntimeStatistics["resolution"];
  readonly timezone: string;
}) {
  /** 用合并后的最小/最大范围和平均线展示 RSS 事实，不推断异常。 */
  const visible = compactGaugePoints(points, 96);
  if (visible.length === 0) {
    return (
      <section className="runtime-statistics__surface runtime-statistics__rss">
        <header>
          <strong>RSS 时序</strong>
          <span>等待首个聚合时间桶</span>
        </header>
        <div className="runtime-statistics__rss-empty">
          当前值已显示在摘要中；形成聚合桶后再绘制变化范围。
        </div>
      </section>
    );
  }

  const values = visible.flatMap((point) => [point.minimum, point.maximum]);
  const minimum = Math.min(...values);
  const maximum = Math.max(...values);
  const spread = maximum - minimum;
  const x = (index: number) =>
    visible.length === 1 ? 480 : 30 + (index / (visible.length - 1)) * 900;
  const y = (value: number) =>
    spread === 0 ? 74 : 130 - ((value - minimum) / spread) * 106;
  const averagePoints = visible
    .map((point, index) => `${x(index)},${y(point.average)}`)
    .join(" ");
  const first = visible[0];
  const last = visible.at(-1)!;

  return (
    <section className="runtime-statistics__surface runtime-statistics__rss">
      <header>
        <strong>RSS 时序</strong>
        <span>
          {resolution === "hour" ? "小时" : "每日"}桶 · 范围与平均值 · 不自动判异常
        </span>
      </header>
      <div className="runtime-statistics__rss-chart">
        <svg
          viewBox="0 0 960 150"
          role="img"
          aria-label={`Sidecar RSS 从 ${formatBytes(minimum)} 到 ${formatBytes(maximum)}`}
        >
          <line className="rss-grid" x1="30" y1="24" x2="930" y2="24" />
          <line className="rss-grid" x1="30" y1="77" x2="930" y2="77" />
          <line className="rss-grid" x1="30" y1="130" x2="930" y2="130" />
          {visible.map((point, index) => (
            <line
              className="rss-range"
              key={point.bucket_start}
              x1={x(index)}
              x2={x(index)}
              y1={y(point.maximum)}
              y2={y(point.minimum)}
            />
          ))}
          {visible.length > 1 && (
            <polyline className="rss-average" points={averagePoints} />
          )}
          {visible.map((point, index) => (
            <circle
              className="rss-point"
              key={`${point.bucket_start}-average`}
              cx={x(index)}
              cy={y(point.average)}
              r={visible.length === 1 ? 4 : 2}
            />
          ))}
        </svg>
        <div className="runtime-statistics__rss-axis">
          <span>{formatShortTimestamp(first.bucket_start, timezone)}</span>
          <span>{formatShortTimestamp(last.bucket_start, timezone)}</span>
        </div>
      </div>
      <div className="runtime-statistics__rss-facts">
        <span>最低 <strong>{formatBytes(minimum)}</strong></span>
        <span>最高 <strong>{formatBytes(maximum)}</strong></span>
        <span>采样 <strong>{formatInteger(points.reduce((sum, point) => sum + point.sample_size, 0))}</strong></span>
        {points.length > visible.length && <span>相邻桶已合并以适配宽度</span>}
        {visible.length === 1 && <span>只有一个时间桶，尚无趋势基线</span>}
      </div>
    </section>
  );
}

function compactGaugePoints(
  points: readonly RuntimeGaugePoint[],
  limit: number,
): RuntimeGaugePoint[] {
  /** 仅为绘图合并相邻桶，API 原始统计仍完整保留。 */
  if (points.length <= limit) return [...points];
  const groupSize = Math.ceil(points.length / limit);
  const result: RuntimeGaugePoint[] = [];
  for (let index = 0; index < points.length; index += groupSize) {
    const group = points.slice(index, index + groupSize);
    const sampleSize = group.reduce((sum, point) => sum + point.sample_size, 0);
    result.push({
      bucket_start: group[0].bucket_start,
      minimum: Math.min(...group.map((point) => point.minimum)),
      maximum: Math.max(...group.map((point) => point.maximum)),
      average:
        group.reduce(
          (sum, point) => sum + point.average * point.sample_size,
          0,
        ) / Math.max(sampleSize, 1),
      sample_size: sampleSize,
    });
  }
  return result;
}

function Summary({ data }: { readonly data: RuntimeStatistics }) {
  /** 保持设计稿的四格深色摘要，不把单次 RSS 画成告警。 */
  const sessions = data.sqlite.files.find((item) => item.name === "sessions.db");
  return (
    <div className="runtime-statistics__summary">
      <SummaryItem
        label="SIDECAR 本次运行"
        value={formatDuration(data.sidecar.uptime.value)}
        meta={`${formatInteger(data.sidecar.uptime.sample_size)} 次采样`}
      />
      <SummaryItem
        label="SIDECAR RSS"
        value={formatBytes(data.sidecar.rss.value)}
        meta={
          data.sidecar.rss.sample_size <= 1
            ? "只有一个样本，尚无基线"
            : `${formatInteger(data.sidecar.rss.sample_size)} 次采样 · 不自动判异常`
        }
      />
      <SummaryItem
        label="会话数据库"
        value={formatBytes(sessions?.total_bytes ?? null)}
        meta="sessions.db · 主库与 WAL 合计"
      />
      <SummaryItem
        label="上次干净退出"
        value={formatTimestamp(data.last_clean_exit_at, data.timezone)}
        meta={`重启 ${data.sidecar.restart_count} · 异常退出 ${data.sidecar.abnormal_exit_count}`}
      />
    </div>
  );
}

function SummaryItem({
  label,
  value,
  meta,
}: {
  readonly label: string;
  readonly value: string;
  readonly meta: string;
}) {
  /** 渲染一个运行摘要事实。 */
  return (
    <div className="runtime-statistics__summary-item">
      <span>{label}</span>
      <strong className={value === "待采集" ? "is-muted" : undefined}>{value}</strong>
      <small>{meta}</small>
    </div>
  );
}

function DistributionTable({
  title,
  note,
  rows,
}: {
  readonly title: string;
  readonly note: string;
  readonly rows: readonly RuntimeDistribution[];
}) {
  /** 用同一表格展示生命周期或四层 owner 的可比分布。 */
  return (
    <section className="runtime-statistics__surface">
      <header><strong>{title}</strong><span>{note}</span></header>
      <div className="runtime-statistics__table-scroll">
        <table>
          <thead><tr><th>操作</th><th>耗时</th><th>样本 / 错误</th><th>状态</th></tr></thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.operation}>
                <td>{row.label}</td>
                <td className="is-mono">{formatDistribution(row)}</td>
                <td className="is-mono">
                  n={row.sample_size} · 错误 {row.error_count}
                </td>
                <td><Quality quality={row.quality} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function SignalsTable({ data }: { readonly data: RuntimeStatistics }) {
  /** 汇总 FastAPI、SSE 和 SQLite 的关键连接信号。 */
  const sseFirst = findDistribution(data.sse.operations, "sse.first_event");
  const sqliteP95 = largestP95(data.sqlite.operations);
  const sqliteSamples = sumSamples(data.sqlite.operations);
  return (
    <section className="runtime-statistics__surface">
      <header><strong>数据与连接</strong><span>不保存 SQL、路径或正文</span></header>
      <div className="runtime-statistics__table-scroll">
        <table>
          <thead><tr><th>信号</th><th>当前值</th><th>样本</th><th>状态</th></tr></thead>
          <tbody>
            <SignalRow
              label="SQLite busy / locked"
              value={`${data.sqlite.busy_count} / ${data.sqlite.locked_count}`}
              samples={sqliteSamples}
              quality={data.sqlite.quality}
            />
            <SignalRow
              label="SQLite 各操作最大 p95"
              value={
                sqliteP95?.p95_ms !== null && sqliteP95 !== undefined
                  ? `p95 ${formatMilliseconds(sqliteP95.p95_ms)}`
                  : sqliteSamples > 0
                    ? "样本不足"
                    : "待采集"
              }
              samples={sqliteP95?.sample_size ?? 0}
              quality={sqliteP95?.quality ?? "unavailable"}
            />
            <SignalRow
              label="SSE 断线 / 重连"
              value={`${data.sse.disconnect_count} / ${data.sse.reconnect_count}`}
              samples={data.sse.connect_count}
              quality={data.sse.quality}
            />
            <SignalRow
              label="SSE 首事件延迟"
              value={sseFirst ? formatDistribution(sseFirst) : "待采集"}
              samples={sseFirst?.sample_size ?? 0}
              quality={sseFirst?.quality ?? "unavailable"}
            />
            {data.fastapi.map((row) => (
              <SignalRow
                key={row.operation}
                label={row.label}
                value={formatDistribution(row)}
                samples={row.sample_size}
                quality={row.quality}
              />
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function SignalRow({
  label,
  value,
  samples,
  quality,
}: {
  readonly label: string;
  readonly value: string;
  readonly samples: number;
  readonly quality: StatisticsQuality;
}) {
  /** 渲染数据与连接表中的一个受控信号。 */
  return (
    <tr>
      <td>{label}</td>
      <td className="is-mono">{value}</td>
      <td className="is-mono">n={samples}</td>
      <td><Quality quality={quality} /></td>
    </tr>
  );
}

function DatabaseFiles({
  files,
}: {
  readonly files: readonly DatabaseFileStatistics[];
}) {
  /** 展示固定文件名与主库/WAL 体积，不接收或显示路径。 */
  return (
    <section className="runtime-statistics__surface runtime-statistics__files">
      <header><strong>数据库文件</strong><span>当前文件大小 · 不代表查询性能</span></header>
      <div className="runtime-statistics__table-scroll">
        <table>
          <thead><tr><th>文件</th><th>总大小</th><th>主库 / WAL</th><th>所有者</th></tr></thead>
          <tbody>
            {files.map((file) => (
              <tr key={file.name}>
                <td className="is-mono">{file.name}</td>
                <td className="is-mono">{formatBytes(file.total_bytes)}</td>
                <td className="is-mono">{formatBytes(file.database_bytes)} / {formatBytes(file.wal_bytes)}</td>
                <td>{DATABASE_OWNER_LABELS[file.owner] ?? file.owner}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function Quality({ quality }: { readonly quality: StatisticsQuality }) {
  /** 把统计质量转换成低噪声文字状态。 */
  return (
    <span className={`runtime-statistics__quality is-${quality}`}>
      {QUALITY_LABELS[quality]}
    </span>
  );
}

function formatDistribution(row: RuntimeDistribution): string {
  /** 优先显示 p95；样本更足时补 p99，较少时回退 p50。 */
  if (row.p99_ms !== null) return `p99 ${formatMilliseconds(row.p99_ms)}`;
  if (row.p95_ms !== null) return `p95 ${formatMilliseconds(row.p95_ms)}`;
  if (row.p50_ms !== null) return `p50 ${formatMilliseconds(row.p50_ms)}`;
  return row.sample_size > 0 ? "样本不足" : "待采集";
}

function findDistribution(
  rows: readonly RuntimeDistribution[],
  operation: string,
): RuntimeDistribution | undefined {
  /** 按固定 operation 查找一条分布。 */
  return rows.find((row) => row.operation === operation);
}

function largestP95(
  rows: readonly RuntimeDistribution[],
): RuntimeDistribution | undefined {
  /** 返回 p95 最大的 SQLite 操作，避免把任意第一行冒充整体代表。 */
  return rows
    .filter((row) => row.p95_ms !== null)
    .sort((left, right) => (right.p95_ms ?? 0) - (left.p95_ms ?? 0))[0];
}

function sumSamples(rows: readonly RuntimeDistribution[]): number {
  /** 合计一组 operation 的样本量。 */
  return rows.reduce((total, row) => total + row.sample_size, 0);
}

function formatMilliseconds(value: number): string {
  /** 小于一秒显示毫秒，否则显示两位小数秒。 */
  return value < 1_000 ? `${Math.round(value)} ms` : `${(value / 1_000).toFixed(2)} s`;
}

function formatDuration(value: number | null): string {
  /** 把毫秒 uptime 转成紧凑分钟或小时。 */
  if (value === null) return "待采集";
  if (value < 60_000) return `${Math.floor(value / 1_000)} 秒`;
  const minutes = Math.floor(value / 60_000);
  if (minutes < 60) return `${minutes} 分钟`;
  const hours = Math.floor(minutes / 60);
  return `${hours} 小时 ${minutes % 60} 分`;
}

function formatBytes(value: number | null): string {
  /** 用二进制单位展示文件和 RSS 字节数。 */
  if (value === null) return "待采集";
  if (value < 1_024) return `${formatInteger(value)} B`;
  if (value < 1_048_576) return `${(value / 1_024).toFixed(1)} KiB`;
  if (value < 1_073_741_824) return `${(value / 1_048_576).toFixed(2)} MiB`;
  return `${(value / 1_073_741_824).toFixed(2)} GiB`;
}

function formatTimestamp(value: string | null, timezone: string): string {
  /** 按页面时区展示退出时刻。 */
  if (value === null) return "待采集";
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: timezone,
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(value));
}

function formatShortTimestamp(value: string, timezone: string): string {
  /** 按页面时区把 RSS 横轴时刻压缩成月日时分。 */
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "时间未知";
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: timezone,
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function formatInteger(value: number): string {
  /** 使用中文环境千分位格式化整数。 */
  return new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 0 }).format(value);
}
