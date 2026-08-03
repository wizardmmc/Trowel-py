/** 纯 props 展示 Memory 的归因、检索、效果、召回和资产统计。 */

import { useId } from "react";
import type {
  MemoryRatio,
  MemorySource,
  MemoryStatistics,
  StatisticsQuality,
} from "../domain/types";
import "./statistics.css";

export interface MemoryStatisticsPanelProps {
  readonly data: MemoryStatistics | null;
  readonly loading: boolean;
  readonly error: string | null;
}

const QUALITY_LABELS: Readonly<Record<StatisticsQuality, string>> = {
  reliable: "数据完整",
  partial: "部分数据",
  unavailable: "数据不可用",
};

const SOURCE_LABELS: Readonly<Record<string, string>> = {
  access: "搜索与读取",
  outcomes: "使用反馈",
  judgements: "会话判效",
  notes: "Note 资产",
  dictionary: "Dictionary",
};

export function MemoryStatisticsPanel({
  data,
  loading,
  error,
}: MemoryStatisticsPanelProps) {
  return (
    <section className="memory-statistics" aria-label="Memory 统计">
      {error && <div className="memory-statistics__notice is-error">{error}</div>}
      {loading && !data && (
        <div className="memory-statistics__notice">正在读取 Memory 统计…</div>
      )}
      {!loading && !data && (
        <div className="memory-statistics__empty">
          <strong>Memory 统计数据不可用</strong>
          <span>当前时间窗没有可读取的 Memory 来源</span>
        </div>
      )}

      {data && (
        <>
          <FactStrip data={data} />
          <Funnel data={data} />
          <div className="memory-statistics__two-column">
            <EffectPanel data={data} />
            <AssetsPanel data={data} />
          </div>
          <SourcesTable sources={data.sources} timezone={data.timezone} />
        </>
      )}
    </section>
  );
}

function FactStrip({ data }: { readonly data: MemoryStatistics }) {
  return (
    <div className="memory-statistics__facts">
      <Fact
        label="归因覆盖"
        value={formatPercent(data.attribution.coverage.ratio)}
        meta={formatRatioCounts(data.attribution.coverage)}
        explanation="已确认会话归属的访问记录 / 全部可解码访问记录。"
      />
      <Fact
        label="命中后读取"
        value={formatRatioCounts(data.retrieval.read_rate)}
        meta={`${formatPercent(data.retrieval.read_rate.ratio)} · 候选命中是分母`}
        explanation="打开读取次数 / 搜索返回的 Note 候选数。它不是搜索调用成功率。"
      />
      <Fact
        label="HELPFUL"
        value={formatRatioCounts(data.effect.helpful_rate)}
        meta={`harmful ${data.effect.harmful} · unused ${data.effect.unused}`}
        explanation="helpful Note-session 对 / helpful、harmful、unused 的合计。unknown 不进入分母。"
      />
      <Fact
        label="RECALL MISS"
        value={formatRatioCounts(data.recall.miss_rate)}
        meta={`检索 ${data.recall.retrieval_miss} · awareness ${data.recall.awareness_miss}`}
        explanation="retrieval miss 与 awareness miss 合计 / 已判效用户会话。一个会话可能有多条 miss。"
      />
    </div>
  );
}

function Fact({
  label,
  value,
  meta,
  explanation,
}: {
  readonly label: string;
  readonly value: string;
  readonly meta: string;
  readonly explanation: string;
}) {
  const tooltipId = useId();
  return (
    <div className="memory-statistics__fact">
      <div className="memory-statistics__fact-label">
        <span>{label}</span>
        <button
          type="button"
          aria-label={`解释${label}`}
          aria-describedby={tooltipId}
        >
          ?
        </button>
        <span id={tooltipId} role="tooltip" className="memory-statistics__tooltip">
          {explanation}
        </span>
      </div>
      <strong>{value}</strong>
      <small>{meta}</small>
    </div>
  );
}

function Funnel({ data }: { readonly data: MemoryStatistics }) {
  const effectDenominator = data.effect.helpful_rate.denominator;
  const maximum = Math.max(
    data.retrieval.search_hits,
    data.retrieval.reads,
    effectDenominator,
    data.effect.helpful,
    1,
  );
  const steps = [
    ["搜索命中", data.retrieval.search_hits],
    ["打开读取", data.retrieval.reads],
    ["效果已判定", effectDenominator],
    ["判断为 helpful", data.effect.helpful],
  ] as const;
  return (
    <section className="memory-statistics__surface memory-statistics__funnel-section">
      <header>
        <strong>从找到到生效</strong>
        <span>四种口径不合并成一个百分比</span>
      </header>
      <div className="memory-statistics__funnel">
        <div className="memory-statistics__funnel-track">
          {steps.map(([label, value]) => (
            <div className="memory-statistics__funnel-step" key={label}>
              <strong>{formatInteger(value)}</strong>
              <span>{label}</span>
              <div aria-hidden="true">
                <i style={{ width: `${(value / maximum) * 100}%` }} />
              </div>
            </div>
          ))}
        </div>
        <p>
          搜索调用 {formatInteger(data.retrieval.search_calls)} 次 · 非空命中 {formatInteger(data.retrieval.nonempty_search_calls)} 次 · 空结果 {formatInteger(data.retrieval.empty_search_calls)} 次 · {formatInteger(data.retrieval.read_sessions)} 个用户会话实际读取过 Note
        </p>
      </div>
    </section>
  );
}

function EffectPanel({ data }: { readonly data: MemoryStatistics }) {
  const denominator = data.effect.helpful_rate.denominator;
  return (
    <section className="memory-statistics__surface">
      <header>
        <strong>效果与召回</strong>
        <span>判断覆盖 {formatPercent(data.effect.judgement_coverage.ratio)}</span>
      </header>
      <div className="memory-statistics__metrics">
        <Metric
          label="helpful"
          detail="Note-session 对"
          value={`${formatInteger(data.effect.helpful)} / ${formatInteger(denominator)}`}
          percent={ratioPercent(data.effect.helpful, denominator)}
          tone="success"
        />
        <Metric
          label="unused"
          detail="已判定但未形成效果"
          value={`${formatInteger(data.effect.unused)} / ${formatInteger(denominator)}`}
          percent={ratioPercent(data.effect.unused, denominator)}
          tone="warning"
        />
        <Metric
          label="harmful"
          detail="判定为造成误导"
          value={`${formatInteger(data.effect.harmful)} / ${formatInteger(denominator)}`}
          percent={ratioPercent(data.effect.harmful, denominator)}
          tone="error"
        />
        <Metric
          label="unknown"
          detail="不进入效果质量分母"
          value={`${formatInteger(data.effect.unknown)} 个单独样本`}
          percent={0}
          tone="muted"
        />
        <Metric
          label="recall miss"
          detail={`检索 miss ${data.recall.retrieval_miss} · awareness miss ${data.recall.awareness_miss}`}
          value={formatRatioCounts(data.recall.miss_rate)}
          percent={(data.recall.miss_rate.ratio ?? 0) * 100}
          tone="warning"
        />
      </div>
      <footer className="memory-statistics__surface-footer">
        <QualityBadge quality={data.effect.quality} />
        <span>{formatInteger(data.effect.judged_user_sessions)} 个已判效用户会话</span>
      </footer>
    </section>
  );
}

function Metric({
  label,
  detail,
  value,
  percent,
  tone,
}: {
  readonly label: string;
  readonly detail: string;
  readonly value: string;
  readonly percent: number;
  readonly tone: "success" | "warning" | "error" | "muted";
}) {
  return (
    <div className="memory-statistics__metric">
      <span>
        <strong>{label}</strong>
        <small>{detail}</small>
      </span>
      <span className="memory-statistics__meter" aria-hidden="true">
        <i className={`is-${tone}`} style={{ width: `${Math.min(percent, 100)}%` }} />
      </span>
      <b>{value}</b>
    </div>
  );
}

function AssetsPanel({ data }: { readonly data: MemoryStatistics }) {
  return (
    <section className="memory-statistics__surface">
      <header>
        <strong>Memory 资产</strong>
        <span>截至 {data.assets.as_of}</span>
      </header>
      <div className="memory-statistics__table-scroll">
        <table>
          <thead>
            <tr><th>项目</th><th>数量 / 状态</th><th>说明</th></tr>
          </thead>
          <tbody>
            <AssetRow label="Active Note" value={formatInteger(data.assets.active_notes)} detail="当前可检索 Note" />
            <AssetRow label="原始读取" value={formatInteger(data.assets.raw_reads)} detail="全量 access log" />
            <AssetRow label="原始 harmful outcome" value={formatInteger(data.assets.raw_harmful_outcomes)} detail="不等于当前判效样本" />
            <AssetRow label="已矛盾或被替代" value={formatInteger(data.assets.contradicted_or_superseded)} detail="当前 north-star 统计" />
            <AssetRow label="harmful 高值 Note" value={formatInteger(data.assets.harmful_high_notes)} detail="达到当前退休阈值" />
            <AssetRow label="Dictionary" value={dictionaryLabel(data.assets.dictionary_status)} detail={formatTimestamp(data.assets.dictionary_updated_at, data.timezone)} />
          </tbody>
        </table>
      </div>
      <footer className="memory-statistics__surface-footer">
        <QualityBadge quality={data.assets.quality} />
        <span>资产是当前快照，不按查询日期倒推历史库存</span>
      </footer>
    </section>
  );
}

function AssetRow({
  label,
  value,
  detail,
}: {
  readonly label: string;
  readonly value: string;
  readonly detail: string;
}) {
  return <tr><td>{label}</td><td className="is-mono">{value}</td><td>{detail}</td></tr>;
}

function SourcesTable({
  sources,
  timezone,
}: {
  readonly sources: Readonly<Record<string, MemorySource>>;
  readonly timezone: string;
}) {
  return (
    <section className="memory-statistics__surface memory-statistics__sources">
      <header>
        <strong>数据范围</strong>
        <span>旧记录没有稳定时间时不归入所选日期</span>
      </header>
      <div className="memory-statistics__table-scroll">
        <table>
          <thead><tr><th>来源</th><th>样本量</th><th>来源更新</th><th>样本范围</th><th>未知时间</th><th>质量</th></tr></thead>
          <tbody>
            {Object.entries(sources).map(([name, source]) => (
              <tr key={name}>
                <td>{SOURCE_LABELS[name] ?? name}</td>
                <td className="is-mono">{formatInteger(source.sample_size)}</td>
                <td>{formatTimestamp(source.updated_at, timezone)}</td>
                <td>{formatRange(source, timezone)}</td>
                <td className="is-mono">{formatInteger(source.unknown_time_records)}</td>
                <td><QualityBadge quality={source.quality} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function QualityBadge({ quality }: { readonly quality: StatisticsQuality }) {
  return <span className={`memory-statistics__quality is-${quality}`}>{QUALITY_LABELS[quality]}</span>;
}

function formatRatioCounts(value: MemoryRatio): string {
  return `${formatInteger(value.numerator)} / ${formatInteger(value.denominator)}`;
}

function formatPercent(value: number | null): string {
  if (value === null) return "不可用";
  return `${new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 }).format(value * 100)}%`;
}

function formatInteger(value: number): string {
  return new Intl.NumberFormat("zh-CN").format(value);
}

function ratioPercent(numerator: number, denominator: number): number {
  return denominator ? (numerator / denominator) * 100 : 0;
}

function dictionaryLabel(status: "consistent" | "stale" | "missing"): string {
  if (status === "consistent") return "最新";
  if (status === "stale") return "需要重建";
  return "未生成";
}

function formatRange(source: MemorySource, timezone: string): string {
  if (!source.sample_start || !source.sample_end) return "无可归期样本";
  return `${formatTimestamp(source.sample_start, timezone)} 至 ${formatTimestamp(source.sample_end, timezone)}`;
}

function formatTimestamp(value: string | null, timezone: string): string {
  if (!value) return "时间未知";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "时间未知";
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: timezone,
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(parsed);
}
