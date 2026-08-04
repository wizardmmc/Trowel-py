/** 把 statistics store 的日期、筛选、分页和选中态接到调用详情展示组件。 */

import { useEffect } from "react";
import { useStore } from "zustand";
import type { StoreApi } from "zustand/vanilla";
import { statisticsStore, type StatisticsState } from "../application/store";
import { CallStatisticsPanel } from "./CallStatisticsPanel";

export interface CallStatisticsViewProps {
  readonly store?: StoreApi<StatisticsState>;
}

/** 订阅调用统计状态，并把日期、筛选、分页和选中动作接到纯展示组件。 */
export function CallStatisticsView({
  store = statisticsStore,
}: CallStatisticsViewProps) {
  const calls = useStore(store, (state) => state.calls);
  const callDetail = useStore(store, (state) => state.callDetail);
  const callFilters = useStore(store, (state) => state.callFilters);
  const selectedSpanId = useStore(store, (state) => state.selectedCallSpanId);
  const callsLoading = useStore(store, (state) => state.callsLoading);
  const callsLoadingMore = useStore(store, (state) => state.callsLoadingMore);
  const callsError = useStore(store, (state) => state.callsError);
  const detailLoading = useStore(store, (state) => state.callDetailLoading);
  const detailError = useStore(store, (state) => state.callDetailError);
  const dateRange = useStore(store, (state) => state.dateRange);
  const refreshCalls = useStore(store, (state) => state.refreshCalls);
  const setCallFilters = useStore(store, (state) => state.setCallFilters);
  const selectCall = useStore(store, (state) => state.selectCall);
  const loadMoreCalls = useStore(store, (state) => state.loadMoreCalls);

  useEffect(() => {
    void refreshCalls();
  }, [
    dateRange.startDate,
    dateRange.endDate,
    dateRange.timezone,
    callFilters.component,
    callFilters.operation,
    callFilters.runtime,
    callFilters.status,
    callFilters.minimumDurationMs,
    refreshCalls,
  ]);

  return (
    <CallStatisticsPanel
      calls={calls}
      detail={callDetail}
      filters={callFilters}
      selectedSpanId={selectedSpanId}
      loading={callsLoading}
      error={callsError}
      detailLoading={detailLoading}
      detailError={detailError}
      loadingMore={callsLoadingMore}
      onFiltersChange={setCallFilters}
      onSelectCall={(call) => void selectCall(call)}
      onLoadMore={() => void loadMoreCalls()}
    />
  );
}
