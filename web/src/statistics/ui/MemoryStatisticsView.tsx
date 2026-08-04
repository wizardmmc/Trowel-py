/** 把 statistics store 的日期和请求状态接到 Memory 展示组件。 */

import { useEffect } from "react";
import { useStore } from "zustand";
import type { StoreApi } from "zustand/vanilla";
import {
  statisticsStore,
  type StatisticsState,
} from "../application/store";
import { MemoryStatisticsPanel } from "./MemoryStatisticsPanel";

export interface MemoryStatisticsViewProps {
  readonly store?: StoreApi<StatisticsState>;
}

export function MemoryStatisticsView({
  store = statisticsStore,
}: MemoryStatisticsViewProps) {
  const data = useStore(store, (state) => state.memory);
  const loading = useStore(store, (state) => state.memoryLoading);
  const error = useStore(store, (state) => state.memoryError);
  const dateRange = useStore(store, (state) => state.dateRange);
  const refreshMemory = useStore(store, (state) => state.refreshMemory);

  useEffect(() => {
    void refreshMemory();
  }, [
    dateRange.startDate,
    dateRange.endDate,
    dateRange.timezone,
    refreshMemory,
  ]);

  return (
    <MemoryStatisticsPanel data={data} loading={loading} error={error} />
  );
}
