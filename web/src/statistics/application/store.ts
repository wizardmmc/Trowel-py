/** 统一保存统计页签、日期范围和 telemetry 查询状态。 */

import { createStore } from "zustand/vanilla";
import type {
  StatisticsDateRange,
  StatisticsResolution,
  StatisticsTab,
  TelemetryStatistics,
} from "../domain/types";
import { fetchTelemetryStatistics } from "../transport/api";

export interface StatisticsApi {
  readonly fetchTelemetry: (
    range: StatisticsDateRange,
    resolution: StatisticsResolution,
  ) => Promise<TelemetryStatistics>;
}

export interface StatisticsState {
  readonly activeTab: StatisticsTab;
  readonly dateRange: StatisticsDateRange;
  readonly resolution: StatisticsResolution;
  readonly telemetry: TelemetryStatistics | null;
  readonly loading: boolean;
  readonly error: string | null;
  readonly setActiveTab: (tab: StatisticsTab) => void;
  readonly setDateRange: (range: StatisticsDateRange) => void;
  readonly setResolution: (resolution: StatisticsResolution) => void;
  readonly refreshTelemetry: () => Promise<void>;
}

const defaultApi: StatisticsApi = {
  fetchTelemetry: fetchTelemetryStatistics,
};

export function createStatisticsStore(api: StatisticsApi = defaultApi) {
  let latestRequest = 0;
  return createStore<StatisticsState>((set, get) => ({
    activeTab: "overview",
    dateRange: initialDateRange(),
    resolution: "hour",
    telemetry: null,
    loading: false,
    error: null,
    setActiveTab: (activeTab) => set({ activeTab }),
    setDateRange: (dateRange) => {
      latestRequest += 1;
      set({ dateRange, telemetry: null, loading: false, error: null });
    },
    setResolution: (resolution) => {
      latestRequest += 1;
      set({ resolution, telemetry: null, loading: false, error: null });
    },
    refreshTelemetry: async () => {
      const request = ++latestRequest;
      const { dateRange, resolution } = get();
      set({ loading: true, error: null });
      try {
        const telemetry = await api.fetchTelemetry(dateRange, resolution);
        if (request !== latestRequest) return;
        set({ telemetry, loading: false });
      } catch (error) {
        if (request !== latestRequest) return;
        set({
          loading: false,
          error: error instanceof Error ? error.message : "统计数据读取失败",
        });
      }
    },
  }));
}

export const statisticsStore = createStatisticsStore();

function initialDateRange(now: Date = new Date()): StatisticsDateRange {
  const date = localIsoDate(now);
  return {
    startDate: date,
    endDate: date,
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
  };
}

function localIsoDate(value: Date): string {
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, "0");
  const day = String(value.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}
