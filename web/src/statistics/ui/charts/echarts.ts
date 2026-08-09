/** 按需注册 Statistics 图表使用的 ECharts 模块和 SVG 渲染器。 */

import { LineChart, type LineSeriesOption } from "echarts/charts";
import {
  AriaComponent,
  GridComponent,
  TooltipComponent,
  type AriaComponentOption,
  type GridComponentOption,
  type TooltipComponentOption,
} from "echarts/components";
import {
  init,
  use as registerEChartsModules,
  type ComposeOption,
  type ECharts,
} from "echarts/core";
import { SVGRenderer } from "echarts/renderers";

registerEChartsModules([
  LineChart,
  GridComponent,
  TooltipComponent,
  AriaComponent,
  SVGRenderer,
]);

export type StatisticsChartOption = ComposeOption<
  | LineSeriesOption
  | GridComponentOption
  | TooltipComponentOption
  | AriaComponentOption
>;

export type StatisticsChart = ECharts;
export const initializeStatisticsChart = init;
