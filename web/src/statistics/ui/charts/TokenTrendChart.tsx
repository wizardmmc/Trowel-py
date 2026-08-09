/** 用本地打包的 ECharts 展示所选范围 token 平滑趋势和缺失说明。 */

import { useEffect, useMemo, useRef } from "react";
import type { OverviewTokenTrendPoint } from "../../domain/overview";
import type { StatisticsQuality } from "../../domain/types";
import "./chart-palette.css";
import type { StatisticsChart, StatisticsChartOption } from "./echarts";

const QUALITY_LABELS: Readonly<Record<StatisticsQuality, string>> = {
  reliable: "可靠",
  partial: "部分可用",
  unavailable: "不可用",
};

export interface TokenTrendChartProps {
  readonly points: readonly OverviewTokenTrendPoint[];
  readonly timezone: string;
}

export function TokenTrendChart({ points, timezone }: TokenTrendChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const hasAvailablePoint = points.some((point) => point.token_total !== null);
  const option = useMemo(
    () => tokenTrendOption(points, timezone),
    [points, timezone],
  );

  useEffect(() => {
    if (!hasAvailablePoint) return;
    const container = containerRef.current;
    if (!container) return;
    let chart: StatisticsChart | null = null;
    let frame = 0;
    let disposed = false;
    let chartModule: Promise<typeof import("./echarts")> | null = null;

    const render = async (): Promise<void> => {
      const bounds = container.getBoundingClientRect();
      if (bounds.width <= 0 || bounds.height <= 0) return;
      chartModule ??= import("./echarts");
      const { initializeStatisticsChart } = await chartModule;
      if (disposed) return;
      chart ??= initializeStatisticsChart(container, undefined, {
        renderer: "svg",
      });
      chart.setOption(option, { notMerge: true });
      chart.resize();
    };
    frame = window.requestAnimationFrame(() => void render());
    const observer =
      typeof ResizeObserver === "undefined"
        ? null
        : new ResizeObserver(() => void render());
    observer?.observe(container);
    const handleResize = () => void render();
    window.addEventListener("resize", handleResize);
    return () => {
      disposed = true;
      window.cancelAnimationFrame(frame);
      observer?.disconnect();
      window.removeEventListener("resize", handleResize);
      chart?.dispose();
    };
  }, [hasAvailablePoint, option]);

  return (
    <div className="overview-statistics__chart-frame">
      {hasAvailablePoint ? (
        <div
          ref={containerRef}
          className="overview-statistics__chart"
          role="img"
          aria-label="所选时间范围可归因 token 用量趋势图"
        />
      ) : (
        <div className="overview-statistics__chart-empty" role="status">
          所选时间范围 token 水位均不可用
        </div>
      )}
      <div className="overview-statistics__chart-legend" aria-label="图表颜色说明">
        <span className="is-reliable">绿色：Token 水位与 session 来源完整</span>
        <span className="is-partial">
          黄色：Token 为已知小计，或 session 来源不完整
        </span>
      </div>
      <ul className="overview-statistics__chart-summary">
        {points.map((point) => (
          <li key={point.date}>
            <time dateTime={point.date}>{shortDate(point.date)}</time>
            <span>
              {point.token_total === null
                ? "不可用"
                : formatTokens(point.token_total)}
            </span>
            <small>
              token 水位 {point.known_token_session_count} / {point.session_count}
              session · {QUALITY_LABELS[point.quality]}
            </small>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** 保留 null 数据点，并按范围密度与数量级生成坐标轴。 */
function tokenTrendOption(
  points: readonly OverviewTokenTrendPoint[],
  timezone: string,
): StatisticsChartOption {
  const maximum = Math.max(
    0,
    ...points.map((point) => point.token_total ?? 0),
  );
  const unit = tokenAxisUnit(maximum);
  const labelInterval = Math.max(0, Math.ceil(points.length / 8) - 1);
  return {
    animationDuration: 180,
    aria: {
      enabled: true,
      decal: { show: false },
      label: {
        description: `所选时间范围 token 用量趋势，按 ${timezone} 当地日期和 Trowel session 增量归集。`,
      },
    },
    grid: { top: 28, right: 18, bottom: 30, left: 58 },
    tooltip: {
      trigger: "axis",
      confine: true,
      formatter: (raw) => {
        const entry = Array.isArray(raw) ? raw[0] : raw;
        const index =
          entry && typeof entry === "object" && "dataIndex" in entry
            ? Number(entry.dataIndex)
            : 0;
        const point = points[index];
        if (!point) return "";
        const value =
          point.token_total === null
            ? "不可用"
            : `${formatTokens(point.token_total)} token`;
        return [
          point.date,
          value,
          point.quality === "unavailable"
            ? "样本不可用"
            : `token 水位 ${point.known_token_session_count} / ${point.session_count} session`,
          `质量 ${QUALITY_LABELS[point.quality]}`,
        ].join("<br />");
      },
    },
    xAxis: {
      type: "category",
      boundaryGap: false,
      data: points.map((point) => shortDate(point.date)),
      axisTick: { show: false },
      axisLine: { lineStyle: { color: "var(--chart-axis)" } },
      axisLabel: {
        color: "var(--chart-label)",
        fontSize: 10,
        hideOverlap: true,
        interval: labelInterval,
      },
    },
    yAxis: {
      type: "value",
      min: 0,
      name: unit.label ? `Token（${unit.label}）` : "Token",
      nameTextStyle: {
        color: "var(--chart-name)",
        fontSize: 9,
        padding: [0, 0, 0, -8],
      },
      axisLabel: {
        color: "var(--chart-name)",
        fontSize: 10,
        formatter: (value: number) => formatAxisValue(value, unit.divisor),
      },
      splitLine: { lineStyle: { color: "var(--chart-grid)" } },
    },
    series: [
      {
        name: "Token",
        type: "line",
        smooth: 0.36,
        connectNulls: false,
        showSymbol: points.length <= 30,
        symbol: "circle",
        symbolSize: 6,
        lineStyle: { width: 2.2, color: "var(--chart-series-primary)" },
        areaStyle: {
          color: {
            type: "linear",
            x: 0,
            y: 0,
            x2: 0,
            y2: 1,
            colorStops: [
              { offset: 0, color: "var(--chart-area-primary)" },
              { offset: 1, color: "var(--chart-area-transparent)" },
            ],
          },
        },
        emphasis: { focus: "series" },
        data: points.map((point) => ({
          value: point.token_total,
          itemStyle: {
            color:
              point.quality === "reliable"
                ? "var(--chart-quality-reliable)"
                : point.quality === "partial"
                  ? "var(--chart-quality-partial)"
                  : "var(--chart-quality-unavailable)",
            borderColor: "var(--chart-point-border)",
            borderWidth: 1,
          },
        })),
      },
    ],
  };
}

interface TokenAxisUnit {
  /** 坐标轴原始 token 值需要除以的数量级。 */
  readonly divisor: number;
  /** 展示在纵轴标题中的中文单位。 */
  readonly label: "亿" | "万" | "千" | "";
}

/** 根据当前图中的最大值选择纵轴单位，tooltip 仍保留完整紧凑值。 */
function tokenAxisUnit(maximum: number): TokenAxisUnit {
  if (maximum >= 100_000_000) return { divisor: 100_000_000, label: "亿" };
  if (maximum >= 10_000) return { divisor: 10_000, label: "万" };
  if (maximum >= 1_000) return { divisor: 1_000, label: "千" };
  return { divisor: 1, label: "" };
}

/** 纵轴只展示除过单位后的短数字，防止大数挤占绘图区。 */
function formatAxisValue(value: number, divisor: number): string {
  const scaled = value / divisor;
  return Number.isInteger(scaled) ? String(scaled) : scaled.toFixed(1);
}

/** 用紧凑单位展示 token，同时保留小数量的整数形态。 */
function formatTokens(value: number): string {
  if (value >= 100_000_000) return `${(value / 100_000_000).toFixed(2)}亿`;
  if (value >= 10_000) return `${(value / 10_000).toFixed(1)}万`;
  return Math.round(value).toLocaleString();
}

/** 把 ISO 当地日期缩成月日，不经过 UTC 时区转换。 */
function shortDate(value: string): string {
  return value.slice(5).replace("-", "/");
}
