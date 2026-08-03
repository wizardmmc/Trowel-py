/** 把 statistics store 的日期、请求状态和筛选动作接到 Agent 展示组件。 */

import { useEffect } from "react";
import { useStore } from "zustand";
import type { StoreApi } from "zustand/vanilla";
import {
  statisticsStore,
  type StatisticsState,
} from "../application/store";
import { AgentStatisticsPanel } from "./AgentStatisticsPanel";

export interface AgentStatisticsViewProps {
  readonly store?: StoreApi<StatisticsState>;
}

export function AgentStatisticsView({
  store = statisticsStore,
}: AgentStatisticsViewProps) {
  const data = useStore(store, (state) => state.agent);
  const loading = useStore(store, (state) => state.agentLoading);
  const error = useStore(store, (state) => state.agentError);
  const runtimeFilter = useStore(store, (state) => state.runtimeFilter);
  const modelFilter = useStore(store, (state) => state.modelFilter);
  const dateRange = useStore(store, (state) => state.dateRange);
  const refreshAgent = useStore(store, (state) => state.refreshAgent);
  const setRuntimeFilter = useStore(store, (state) => state.setRuntimeFilter);
  const setModelFilter = useStore(store, (state) => state.setModelFilter);

  useEffect(() => {
    void refreshAgent();
  }, [
    dateRange.startDate,
    dateRange.endDate,
    dateRange.timezone,
    refreshAgent,
  ]);

  return (
    <AgentStatisticsPanel
      data={data}
      loading={loading}
      error={error}
      runtimeFilter={runtimeFilter}
      modelFilter={modelFilter}
      onRuntimeFilterChange={setRuntimeFilter}
      onModelFilterChange={setModelFilter}
    />
  );
}
