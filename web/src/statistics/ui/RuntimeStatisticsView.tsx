/** 把 statistics store 的日期和请求状态接到运行统计展示组件。 */

import { useEffect } from "react";
import { useStore } from "zustand";
import type { StoreApi } from "zustand/vanilla";
import {
  statisticsStore,
  type StatisticsState,
} from "../application/store";
import { RuntimeStatisticsPanel } from "./RuntimeStatisticsPanel";

const RUNTIME_REFRESH_INTERVAL_MS = 5_000;

export interface RuntimeStatisticsViewProps {
  readonly store?: StoreApi<StatisticsState>;
}

export function RuntimeStatisticsView({
  store = statisticsStore,
}: RuntimeStatisticsViewProps) {
  const data = useStore(store, (state) => state.runtime);
  const loading = useStore(store, (state) => state.runtimeLoading);
  const error = useStore(store, (state) => state.runtimeError);
  const dateRange = useStore(store, (state) => state.dateRange);
  const refreshRuntime = useStore(store, (state) => state.refreshRuntime);

  useEffect(() => {
    void refreshRuntime();
    const refreshVisiblePage = () => {
      if (document.visibilityState === "visible") void refreshRuntime();
    };
    const interval = window.setInterval(
      refreshVisiblePage,
      RUNTIME_REFRESH_INTERVAL_MS,
    );
    document.addEventListener("visibilitychange", refreshVisiblePage);
    return () => {
      window.clearInterval(interval);
      document.removeEventListener("visibilitychange", refreshVisiblePage);
    };
  }, [
    dateRange.startDate,
    dateRange.endDate,
    dateRange.timezone,
    refreshRuntime,
  ]);

  return (
    <div className="runtime-statistics-view">
      <p className="runtime-statistics-view__refresh">每 5 秒自动刷新</p>
      <RuntimeStatisticsPanel data={data} loading={loading} error={error} />
    </div>
  );
}
