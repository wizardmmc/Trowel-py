/** 把 statistics store 的日期和请求状态接到运行统计展示组件。 */

import { useEffect } from "react";
import { useStore } from "zustand";
import type { StoreApi } from "zustand/vanilla";
import {
  statisticsStore,
  type StatisticsState,
} from "../application/store";
import { RuntimeStatisticsPanel } from "./RuntimeStatisticsPanel";

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
  }, [
    dateRange.startDate,
    dateRange.endDate,
    dateRange.timezone,
    refreshRuntime,
  ]);

  return (
    <RuntimeStatisticsPanel data={data} loading={loading} error={error} />
  );
}
