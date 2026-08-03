/** 统一保存统计页签、日期范围和 telemetry 查询状态。 */

import { createStore } from "zustand/vanilla";
import type {
  StatisticsDateRange,
  AgentRuntimeFilter,
  AgentStatistics,
  StatisticsResolution,
  StatisticsTab,
  TelemetryStatistics,
} from "../domain/types";
import {
  fetchAgentStatistics,
  fetchTelemetryStatistics,
} from "../transport/api";

export interface StatisticsApi {
  readonly fetchAgent: (
    range: StatisticsDateRange,
  ) => Promise<AgentStatistics>;
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
  readonly agent: AgentStatistics | null;
  readonly loading: boolean;
  readonly error: string | null;
  readonly agentLoading: boolean;
  readonly agentError: string | null;
  readonly runtimeFilter: AgentRuntimeFilter;
  readonly modelFilter: string;
  readonly setActiveTab: (tab: StatisticsTab) => void;
  readonly setDateRange: (range: StatisticsDateRange) => void;
  readonly setResolution: (resolution: StatisticsResolution) => void;
  readonly setRuntimeFilter: (runtime: AgentRuntimeFilter) => void;
  readonly setModelFilter: (model: string) => void;
  readonly refreshTelemetry: () => Promise<void>;
  readonly refreshAgent: () => Promise<void>;
}

const defaultApi: StatisticsApi = {
  fetchAgent: fetchAgentStatistics,
  fetchTelemetry: fetchTelemetryStatistics,
};

export function createStatisticsStore(apiOverrides: Partial<StatisticsApi> = {}) {
  const api = { ...defaultApi, ...apiOverrides };
  let latestTelemetryRequest = 0;
  let latestAgentRequest = 0;
  return createStore<StatisticsState>((set, get) => ({
    activeTab: "overview",
    dateRange: initialDateRange(),
    resolution: "hour",
    telemetry: null,
    agent: null,
    loading: false,
    error: null,
    agentLoading: false,
    agentError: null,
    runtimeFilter: "all",
    modelFilter: "all",
    setActiveTab: (activeTab) => set({ activeTab }),
    setDateRange: (dateRange) => {
      latestTelemetryRequest += 1;
      latestAgentRequest += 1;
      set({
        dateRange,
        telemetry: null,
        agent: null,
        loading: false,
        error: null,
        agentLoading: false,
        agentError: null,
        modelFilter: "all",
      });
    },
    setResolution: (resolution) => {
      latestTelemetryRequest += 1;
      set({ resolution, telemetry: null, loading: false, error: null });
    },
    setRuntimeFilter: (runtimeFilter) =>
      set({ runtimeFilter, modelFilter: "all" }),
    setModelFilter: (modelFilter) => set({ modelFilter }),
    refreshTelemetry: async () => {
      const request = ++latestTelemetryRequest;
      const { dateRange, resolution } = get();
      set({ loading: true, error: null });
      try {
        const telemetry = await api.fetchTelemetry(dateRange, resolution);
        if (request !== latestTelemetryRequest) return;
        set({ telemetry, loading: false });
      } catch (error) {
        if (request !== latestTelemetryRequest) return;
        set({
          loading: false,
          error: error instanceof Error ? error.message : "统计数据读取失败",
        });
      }
    },
    refreshAgent: async () => {
      const request = ++latestAgentRequest;
      const { dateRange } = get();
      set({ agentLoading: true, agentError: null });
      try {
        const agent = await api.fetchAgent(dateRange);
        if (request !== latestAgentRequest) return;
        set({ agent, agentLoading: false });
      } catch (error) {
        if (request !== latestAgentRequest) return;
        set({
          agentLoading: false,
          agentError:
            error instanceof Error ? error.message : "Agent 统计数据读取失败",
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
