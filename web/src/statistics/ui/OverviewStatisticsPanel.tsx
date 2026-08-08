/** 纯 props 展示总览事实、所选范围趋势、确定性缺口和会话问题。 */

import type { ReactNode } from "react";
import type {
  OverviewSessionProblem,
  OverviewStatistics,
} from "../domain/overview";
import type { StatisticsQuality } from "../domain/types";
import { CopyButton } from "../../components/ui/CopyButton";
import { TokenTrendChart } from "./charts/TokenTrendChart";
import "./overview-statistics.css";

const QUALITY_LABELS: Readonly<Record<StatisticsQuality, string>> = {
  reliable: "可靠",
  partial: "部分可用",
  unavailable: "不可用",
};

export interface OverviewStatisticsPanelProps {
  readonly data: OverviewStatistics | null;
  readonly loading: boolean;
  readonly error: string | null;
  readonly copiedProblemId?: string | null;
  readonly onCopyProblem: (problem: OverviewSessionProblem) => void;
}

export function OverviewStatisticsPanel({
  data,
  loading,
  error,
  copiedProblemId = null,
  onCopyProblem,
}: OverviewStatisticsPanelProps) {
  if (!data && loading) {
    return (
      <section className="overview-statistics__empty" aria-live="polite">
        正在读取统计总览…
      </section>
    );
  }
  if (!data) {
    return (
      <section
        className={`overview-statistics__empty${error ? " is-error" : ""}`}
        aria-live="polite"
      >
        <strong>统计总览暂不可用</strong>
        <span>{error ?? "当前来源没有可展示事实"}</span>
      </section>
    );
  }

  const memoryAvailable = data.memory.quality !== "unavailable";
  const problemsAvailable = data.session_problems.quality !== "unavailable";
  return (
    <section className="overview-statistics" aria-label="统计总览">
      {error && (
        <div className="overview-statistics__notice is-error">{error}</div>
      )}
      <div className="overview-statistics__facts">
        <OverviewFact
          label="用户 SESSION"
          value={qualityValue(
            data.agent.quality,
            formatInteger(data.agent.user_sessions),
          )}
          meta={
            data.agent.quality === "unavailable"
              ? "会话终态来源不可用"
              : formatStatusSummary(data)
          }
          accent
        />
        <OverviewFact
          label="合计 TOKEN"
          value={qualityValue(
            data.agent.tokens.quality,
            formatTokens(data.agent.tokens.total),
          )}
          meta={`按 runtime 水位差归集 · 含缓存输入 · ${tokenCoverage(data.agent.tokens.known_session_count, data.agent.tokens.session_count)}`}
        />
        <OverviewFact
          label="MEMORY 有效使用"
          value={
            memoryAvailable
              ? formatHelpfulUse(
                  data.memory.helpful,
                  data.memory.judged_effects,
                  data.memory.helpful_rate.ratio,
                )
              : "不可用"
          }
          meta="helpful / 已判定 · 分母保持独立"
        />
        <OverviewFact
          label="会话复盘问题"
          value={
            problemsAvailable
              ? `${formatInteger(data.session_problems.problem_count)} / ${formatInteger(data.session_problems.reviewed_session_count)}`
              : "不可用"
          }
          meta={
            problemsAvailable ? "非空问题 / 已处理会话" : "来源不可用"
          }
        />
      </div>

      <div className="overview-statistics__primary-grid">
        <OverviewSurface
          title="每日 token 用量"
          meta={trendRangeLabel(data.token_trend, data.timezone)}
        >
          <TokenTrendChart points={data.token_trend} timezone={data.timezone} />
        </OverviewSurface>
        <OverviewSurface
          title="运行状态与采集缺口"
          meta="确定性规则 · 不判断原因"
        >
          <ul className="overview-statistics__statuses">
            {data.statuses.length === 0 && (
              <li className="is-info">
                <span className="overview-statistics__status-mark" />
                <div>
                  <strong>当前没有需要单列的状态</strong>
                  <p>各领域详细样本仍可从对应页签查看。</p>
                </div>
                <small>read model</small>
              </li>
            )}
            {data.statuses.map((status) => (
              <li key={status.code} className={`is-${status.level}`}>
                <span className="overview-statistics__status-mark" />
                <div>
                  <strong>{status.title}</strong>
                  <p>{status.detail}</p>
                </div>
                <small>{status.source}</small>
              </li>
            ))}
          </ul>
        </OverviewSurface>
      </div>

      <div className="overview-statistics__secondary-grid">
        <OverviewSurface
          title="Memory 使用漏斗"
          meta={QUALITY_LABELS[data.memory.quality]}
        >
          <div className="overview-statistics__funnel">
            {[
              ["搜索命中", data.memory.search_hits],
              ["打开读取", data.memory.reads],
              ["效果已判定", data.memory.judged_effects],
              ["判断为 helpful", data.memory.helpful],
            ].map(([label, value]) => (
              <div key={String(label)}>
                <strong>
                  {memoryAvailable ? formatInteger(Number(value)) : "不可用"}
                </strong>
                <span>{label}</span>
                <i
                  style={{
                    width: memoryAvailable
                      ? `${funnelWidth(Number(value), data.memory.search_hits)}%`
                      : "0%",
                  }}
                />
              </div>
            ))}
          </div>
          <p className="overview-statistics__funnel-meta">
            {memoryAvailable ? (
              <>
                判断覆盖 {formatRatio(data.memory.judgement_coverage)} · recall
                miss {formatRatio(data.memory.recall_miss_rate)} · 归因{" "}
                {formatPercent(data.memory.attribution_coverage.ratio)}
              </>
            ) : (
              "判断覆盖不可用 · recall miss 不可用 · 归因不可用"
            )}
          </p>
        </OverviewSurface>

        <OverviewSurface title="本机数据体积" meta="当前文件快照">
          <div className="overview-statistics__table-scroll">
            <table>
              <thead>
                <tr>
                  <th>数据</th>
                  <th>大小</th>
                  <th>Owner</th>
                </tr>
              </thead>
              <tbody>
                {data.database_files.map((file) => (
                  <tr key={file.name}>
                    <td className="is-mono">{file.name}</td>
                    <td className="is-mono">
                      {file.quality === "unavailable"
                        ? "不可用"
                        : formatBytes(file.total_bytes)}
                    </td>
                    <td>{file.owner}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </OverviewSurface>
      </div>

      <OverviewSurface
        title="最近会话问题"
        meta={
          problemsAvailable
            ? `${data.session_problems.problem_count} 条非空问题`
            : "来源不可用"
        }
      >
        {data.session_problems.items.length === 0 ? (
          <div className="overview-statistics__surface-empty">
            {problemsAvailable
              ? "当前时间窗没有非空问题"
              : "会话问题来源不可用"}
          </div>
        ) : (
          <ol className="overview-statistics__problems">
            {data.session_problems.items.map((problem) => (
              <li key={problem.trowel_session_id}>
                <div>
                  <p>{problem.problem_text}</p>
                  <span>
                    <code>{problem.trowel_session_id}</code>
                    <small>{runtimeLabel(problem.runtime)}</small>
                    <time dateTime={problem.closed_at}>
                      {formatTimestamp(problem.closed_at, data.timezone)}
                    </time>
                  </span>
                </div>
                <CopyButton
                  tone="brand"
                  copied={copiedProblemId === problem.trowel_session_id}
                  onClick={() => onCopyProblem(problem)}
                  ariaLabel="复制问题与会话信息"
                />
              </li>
            ))}
          </ol>
        )}
      </OverviewSurface>

      <OverviewSurface
        title="数据来源"
        meta={`${data.sample_size} / 5 个来源可用`}
      >
        <div className="overview-statistics__source-grid">
          {Object.entries(data.sources).map(([name, source]) => (
            <div key={name}>
              <strong>{source.label}</strong>
              <span className={`is-${source.quality}`}>
                {QUALITY_LABELS[source.quality]}
              </span>
              <small>
                n=
                {source.quality === "unavailable"
                  ? "不可用"
                  : formatInteger(source.sample_size)}
              </small>
              <time dateTime={source.freshness.updated_at ?? undefined}>
                {formatTimestamp(source.freshness.updated_at, data.timezone)}
              </time>
            </div>
          ))}
        </div>
      </OverviewSurface>
    </section>
  );
}

/** 展示一项当前时间窗事实，不自行读取或计算业务来源。 */
function OverviewFact({
  label,
  value,
  meta,
  accent = false,
}: {
  readonly label: string;
  readonly value: string;
  readonly meta: string;
  readonly accent?: boolean;
}) {
  return (
    <div className={`overview-statistics__fact${accent ? " is-accent" : ""}`}>
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{meta}</small>
    </div>
  );
}

/** 为真实图表、列表或表格提供统一边界和标题。 */
function OverviewSurface({
  title,
  meta,
  children,
}: {
  readonly title: string;
  readonly meta: string;
  readonly children: ReactNode;
}) {
  return (
    <section className="overview-statistics__surface">
      <header>
        <strong>{title}</strong>
        <span>{meta}</span>
      </header>
      {children}
    </section>
  );
}

/** 质量不可用时隐藏数值，防止零值或旧值冒充事实。 */
function qualityValue(quality: StatisticsQuality, value: string): string {
  return quality === "unavailable" ? "不可用" : value;
}

/** 用固定顺序展示 session 终态，不把状态折成综合分。 */
function formatStatusSummary(data: OverviewStatistics): string {
  const statuses = data.agent.statuses;
  return [
    `${statuses.completed} 完成`,
    `${statuses.running} 运行`,
    `${statuses.interrupted} 中断`,
    `${statuses.failed} 失败`,
    `${statuses.unknown} 未知`,
  ].join(" · ");
}

/** 用紧凑单位展示 token；未知值明确返回不可用。 */
function formatTokens(value: number | null): string {
  if (value === null) return "不可用";
  if (value >= 100_000_000) return `${(value / 100_000_000).toFixed(2)} 亿`;
  if (value >= 10_000) return `${(value / 10_000).toFixed(1)} 万`;
  return formatInteger(value);
}

/** 说明 token 总量覆盖了多少个 session，避免把已知小计当完整总量。 */
function tokenCoverage(known: number, total: number): string {
  if (total === 0) return "无 session 样本";
  return known === total
    ? `${known} / ${total} session 有水位`
    : `已知小计 ${known} / ${total} session`;
}

/** 按当前中文界面格式化非小数计数。 */
function formatInteger(value: number): string {
  return Math.round(value).toLocaleString("zh-CN");
}

/** 展示已有分子分母；没有可靠分母时返回不可用。 */
function formatRatio(value: {
  numerator: number;
  denominator: number;
  quality: StatisticsQuality;
}): string {
  return value.quality === "unavailable"
    ? "不可用"
    : `${formatInteger(value.numerator)} / ${formatInteger(value.denominator)}`;
}

/** 把 0 至 1 的比例展示为一位小数百分比。 */
function formatPercent(value: number | null): string {
  return value === null ? "不可用" : `${(value * 100).toFixed(1)}%`;
}

/** 在保留真实分子分母的同时附上便于扫读的近似比例。 */
function formatHelpfulUse(
  helpful: number,
  judgedEffects: number,
  ratio: number | null,
): string {
  const counts = `${formatInteger(helpful)} / ${formatInteger(judgedEffects)}`;
  return ratio === null ? counts : `${counts} ≈ ${formatPercent(ratio)}`;
}

/** 展示趋势实际覆盖范围，避免标题继续暗示固定七天。 */
function trendRangeLabel(
  points: OverviewStatistics["token_trend"],
  timezone: string,
): string {
  if (points.length === 0) return timezone;
  const start = points[0]?.date;
  const end = points.at(-1)?.date;
  return start === end
    ? `${start} · ${timezone}`
    : `${start} — ${end} · ${timezone}`;
}

/** 把漏斗计数压到 0 至 100 的展示宽度。 */
function funnelWidth(value: number, maximum: number): number {
  return maximum > 0 ? Math.min(100, Math.max(0, (value / maximum) * 100)) : 0;
}

/** 把字节数格式化为 KiB、MiB 或 GiB。 */
function formatBytes(value: number): string {
  if (value >= 1024 ** 3) return `${(value / 1024 ** 3).toFixed(2)} GiB`;
  if (value >= 1024 ** 2) return `${(value / 1024 ** 2).toFixed(2)} MiB`;
  if (value >= 1024) return `${(value / 1024).toFixed(1)} KiB`;
  return `${formatInteger(value)} B`;
}

/** 按用户选择的时区展示来源时间；未知时返回未更新。 */
function formatTimestamp(value: string | null, timezone: string): string {
  if (value === null) return "未更新";
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: timezone,
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

/** 把公开 runtime 值转换为页面名称。 */
function runtimeLabel(runtime: OverviewSessionProblem["runtime"]): string {
  return runtime === "claude_code" ? "Claude Code" : "Codex";
}
