/** 组装 Statistics 标题、共享状态、五个生产页签和 trace 深链接。 */

import { useEffect, useLayoutEffect, useRef } from "react";
import { useStore } from "zustand";
import type { StoreApi } from "zustand/vanilla";
import { statisticsStore, type StatisticsState } from "../application/store";
import type { StatisticsQuality, StatisticsTab } from "../domain/types";
import { AgentStatisticsView } from "./AgentStatisticsView";
import { CallStatisticsView } from "./CallStatisticsView";
import { MemoryStatisticsView } from "./MemoryStatisticsView";
import { OverviewStatisticsView } from "./OverviewStatisticsView";
import { RuntimeStatisticsView } from "./RuntimeStatisticsView";
import { StatisticsTabs } from "./StatisticsTabs";
import "./statistics-workspace.css";

const STATISTICS_TABS = new Set<StatisticsTab>([
  "overview",
  "agent",
  "memory",
  "runtime",
  "calls",
]);
const TRACE_ID_PATTERN = /^[0-9a-f]{32}$/i;
const READ_ONLY_INSPECTION =
  import.meta.env.VITE_TROWEL_INSPECTION_MODE === "1";

export interface StatisticsWorkspaceProps {
  readonly store?: StoreApi<StatisticsState>;
  readonly active?: boolean;
}

export function StatisticsWorkspace({
  store = statisticsStore,
  active = true,
}: StatisticsWorkspaceProps) {
  const deepLinkApplied = useRef(false);
  const activeTab = useStore(store, (state) => state.activeTab);
  const dateRange = useStore(store, (state) => state.dateRange);
  const selectedTraceId = useStore(store, (state) => state.selectedCallTraceId);
  const setActiveTab = useStore(store, (state) => state.setActiveTab);
  const setDateRange = useStore(store, (state) => state.setDateRange);
  const openCallTrace = useStore(store, (state) => state.openCallTrace);
  const pageQuality = useStore(store, currentPageQuality);
  const pageHasData = useStore(store, currentPageHasData);
  const pageLoading = useStore(store, currentPageLoading);

  useLayoutEffect(() => {
    if (deepLinkApplied.current) return;
    deepLinkApplied.current = true;
    const params = new URLSearchParams(window.location.search);
    const requestedTab = params.get("statistics_tab");
    const traceId = params.get("trace_id");
    if (traceId && TRACE_ID_PATTERN.test(traceId)) {
      setActiveTab("calls");
      void openCallTrace(traceId);
      return;
    }
    if (requestedTab && STATISTICS_TABS.has(requestedTab as StatisticsTab)) {
      setActiveTab(requestedTab as StatisticsTab);
    }
  }, [openCallTrace, setActiveTab]);

  useEffect(() => {
    if (!active) return;
    const url = new URL(window.location.href);
    url.searchParams.set("tool", "statistics");
    url.searchParams.set("statistics_tab", activeTab);
    if (activeTab === "calls" && selectedTraceId) {
      url.searchParams.set("trace_id", selectedTraceId);
    } else {
      url.searchParams.delete("trace_id");
    }
    window.history.replaceState(window.history.state, "", url);
  }, [active, activeTab, selectedTraceId]);

  return (
    <main className="statistics-workspace">
      <header className="statistics-workspace__header">
        <div>
          <span>本机观察</span>
          <h1>统计</h1>
        </div>
        {READ_ONLY_INSPECTION && (
          <span className="statistics-workspace__inspection">
            真实数据 · 只读观察
          </span>
        )}
        <span className={`statistics-workspace__quality is-${pageQuality}`}>
          {qualityLabel(pageQuality, pageHasData, pageLoading)}
        </span>
      </header>
      <StatisticsTabs
        activeTab={activeTab}
        dateRange={dateRange}
        onTabChange={setActiveTab}
        onDateRangeChange={setDateRange}
      >
        {activeView(activeTab, store)}
      </StatisticsTabs>
    </main>
  );
}

/** 只挂载当前页，避免后台页签在日期变化时发出无用请求。 */
function activeView(tab: StatisticsTab, store: StoreApi<StatisticsState>) {
  switch (tab) {
    case "overview":
      return <OverviewStatisticsView store={store} />;
    case "agent":
      return <AgentStatisticsView store={store} />;
    case "memory":
      return <MemoryStatisticsView store={store} />;
    case "runtime":
      return <RuntimeStatisticsView store={store} />;
    case "calls":
      return <CallStatisticsView store={store} />;
  }
}

/** 读取当前页最近一次成功数据的质量。 */
function currentPageQuality(state: StatisticsState): StatisticsQuality {
  switch (state.activeTab) {
    case "overview":
      return state.overview?.quality ?? "unavailable";
    case "agent":
      return state.agent?.quality ?? "unavailable";
    case "memory":
      return state.memory?.quality ?? "unavailable";
    case "runtime":
      return state.runtime?.quality ?? "unavailable";
    case "calls":
      return state.calls?.quality ?? "unavailable";
  }
}

/** 判断当前页是否已经拿到一份响应，包括明确 unavailable 的响应。 */
function currentPageHasData(state: StatisticsState): boolean {
  switch (state.activeTab) {
    case "overview":
      return state.overview !== null;
    case "agent":
      return state.agent !== null;
    case "memory":
      return state.memory !== null;
    case "runtime":
      return state.runtime !== null;
    case "calls":
      return state.calls !== null;
  }
}

/** 返回当前页自己的加载状态，避免把已知不可用误写成仍在等待。 */
function currentPageLoading(state: StatisticsState): boolean {
  switch (state.activeTab) {
    case "overview":
      return state.overviewLoading;
    case "agent":
      return state.agentLoading;
    case "memory":
      return state.memoryLoading;
    case "runtime":
      return state.runtimeLoading;
    case "calls":
      return state.callsLoading;
  }
}

/** 把加载阶段和统一质量值转换成页面右上角简短说明。 */
function qualityLabel(
  quality: StatisticsQuality,
  hasData: boolean,
  loading: boolean,
): string {
  if (!hasData) return loading ? "正在读取" : "等待数据";
  if (quality === "reliable") return "数据可靠";
  if (quality === "partial") return "部分可用";
  return "数据不可用";
}
