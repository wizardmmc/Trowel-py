/** 统一保存统计页签、日期范围和 telemetry 查询状态。 */

import { createStore } from "zustand/vanilla";
import type {
  StatisticsDateRange,
  AgentRuntimeFilter,
  AgentStatistics,
  CallDetail,
  CallFilters,
  CallList,
  CallListItem,
  MemoryStatistics,
  RuntimeStatistics,
  StatisticsResolution,
  StatisticsTab,
  TelemetryStatistics,
} from "../domain/types";
import type { OverviewStatistics } from "../domain/overview";
import {
  fetchAgentStatistics,
  fetchCallDetail,
  fetchCallStatistics,
  fetchMemoryStatistics,
  fetchOverviewStatistics,
  fetchRuntimeStatistics,
  fetchTelemetryStatistics,
} from "../transport/api";

export interface StatisticsApi {
  readonly fetchOverview: (
    range: StatisticsDateRange,
  ) => Promise<OverviewStatistics>;
  readonly fetchAgent: (range: StatisticsDateRange) => Promise<AgentStatistics>;
  readonly fetchMemory: (
    range: StatisticsDateRange,
  ) => Promise<MemoryStatistics>;
  readonly fetchTelemetry: (
    range: StatisticsDateRange,
    resolution: StatisticsResolution,
  ) => Promise<TelemetryStatistics>;
  readonly fetchRuntime: (
    range: StatisticsDateRange,
  ) => Promise<RuntimeStatistics>;
  readonly fetchCalls: (
    range: StatisticsDateRange,
    filters: CallFilters,
    cursor?: string,
  ) => Promise<CallList>;
  readonly fetchCallDetail: (traceId: string) => Promise<CallDetail>;
}

export interface StatisticsState {
  readonly activeTab: StatisticsTab;
  readonly dateRange: StatisticsDateRange;
  readonly resolution: StatisticsResolution;
  readonly telemetry: TelemetryStatistics | null;
  readonly overview: OverviewStatistics | null;
  readonly agent: AgentStatistics | null;
  readonly memory: MemoryStatistics | null;
  readonly runtime: RuntimeStatistics | null;
  readonly calls: CallList | null;
  readonly callDetail: CallDetail | null;
  readonly loading: boolean;
  readonly error: string | null;
  readonly overviewLoading: boolean;
  readonly overviewError: string | null;
  readonly agentLoading: boolean;
  readonly agentError: string | null;
  readonly memoryLoading: boolean;
  readonly memoryError: string | null;
  readonly runtimeLoading: boolean;
  readonly runtimeError: string | null;
  readonly callsLoading: boolean;
  readonly callsLoadingMore: boolean;
  readonly callsError: string | null;
  readonly callDetailLoading: boolean;
  readonly callDetailError: string | null;
  readonly callFilters: CallFilters;
  readonly selectedCallSpanId: string | null;
  readonly selectedCallTraceId: string | null;
  readonly deepLinkedCallTraceId: string | null;
  readonly runtimeFilter: AgentRuntimeFilter;
  readonly modelFilter: string;
  readonly setActiveTab: (tab: StatisticsTab) => void;
  readonly setDateRange: (range: StatisticsDateRange) => void;
  readonly setResolution: (resolution: StatisticsResolution) => void;
  readonly setRuntimeFilter: (runtime: AgentRuntimeFilter) => void;
  readonly setModelFilter: (model: string) => void;
  readonly refreshTelemetry: () => Promise<void>;
  readonly refreshOverview: () => Promise<void>;
  readonly refreshAgent: () => Promise<void>;
  readonly refreshMemory: () => Promise<void>;
  readonly refreshRuntime: () => Promise<void>;
  readonly setCallFilters: (filters: CallFilters) => void;
  readonly refreshCalls: () => Promise<void>;
  readonly loadMoreCalls: () => Promise<void>;
  readonly selectCall: (call: CallListItem) => Promise<void>;
  readonly openCallTrace: (traceId: string) => Promise<void>;
}

export const DEFAULT_CALL_FILTERS: CallFilters = {
  component: "all",
  operation: "all",
  runtime: "all",
  status: "all",
  minimumDurationMs: 0,
};

const defaultApi: StatisticsApi = {
  fetchOverview: fetchOverviewStatistics,
  fetchAgent: fetchAgentStatistics,
  fetchMemory: fetchMemoryStatistics,
  fetchTelemetry: fetchTelemetryStatistics,
  fetchRuntime: fetchRuntimeStatistics,
  fetchCalls: fetchCallStatistics,
  fetchCallDetail,
};

/** 创建隔离的统计状态容器，并允许测试替换各数据读取函数。 */
export function createStatisticsStore(
  apiOverrides: Partial<StatisticsApi> = {},
) {
  const api = { ...defaultApi, ...apiOverrides };
  let latestTelemetryRequest = 0;
  let latestOverviewRequest = 0;
  let latestAgentRequest = 0;
  let latestMemoryRequest = 0;
  let latestRuntimeRequest = 0;
  let latestCallsRequest = 0;
  let latestCallDetailRequest = 0;
  return createStore<StatisticsState>((set, get) => {
    const loadCallDetail = async (
      traceId: string,
      spanId: string | null,
    ): Promise<void> => {
      const existing = get().callDetail;
      set({
        selectedCallSpanId: spanId,
        selectedCallTraceId: traceId,
        callDetailError: null,
      });
      if (existing?.trace_id === traceId) return;
      const request = ++latestCallDetailRequest;
      set({ callDetail: null, callDetailLoading: true });
      try {
        const callDetail = await api.fetchCallDetail(traceId);
        if (request !== latestCallDetailRequest) return;
        set({ callDetail, callDetailLoading: false });
      } catch (error) {
        if (request !== latestCallDetailRequest) return;
        set({
          callDetailLoading: false,
          callDetailError:
            error instanceof Error ? error.message : "调用详情读取失败",
        });
      }
    };

    return {
      activeTab: "overview",
      dateRange: initialDateRange(),
      resolution: "hour",
      telemetry: null,
      overview: null,
      agent: null,
      memory: null,
      runtime: null,
      calls: null,
      callDetail: null,
      loading: false,
      error: null,
      overviewLoading: false,
      overviewError: null,
      agentLoading: false,
      agentError: null,
      memoryLoading: false,
      memoryError: null,
      runtimeLoading: false,
      runtimeError: null,
      callsLoading: false,
      callsLoadingMore: false,
      callsError: null,
      callDetailLoading: false,
      callDetailError: null,
      callFilters: DEFAULT_CALL_FILTERS,
      selectedCallSpanId: null,
      selectedCallTraceId: null,
      deepLinkedCallTraceId: null,
      runtimeFilter: "all",
      modelFilter: "all",
      setActiveTab: (activeTab) => set({ activeTab }),
      setDateRange: (dateRange) => {
        latestOverviewRequest += 1;
        latestTelemetryRequest += 1;
        latestAgentRequest += 1;
        latestMemoryRequest += 1;
        latestRuntimeRequest += 1;
        latestCallsRequest += 1;
        latestCallDetailRequest += 1;
        set({
          dateRange,
          telemetry: null,
          overview: null,
          agent: null,
          memory: null,
          runtime: null,
          calls: null,
          callDetail: null,
          loading: false,
          error: null,
          overviewLoading: false,
          overviewError: null,
          agentLoading: false,
          agentError: null,
          memoryLoading: false,
          memoryError: null,
          runtimeLoading: false,
          runtimeError: null,
          callsLoading: false,
          callsLoadingMore: false,
          callsError: null,
          callDetailLoading: false,
          callDetailError: null,
          selectedCallSpanId: null,
          selectedCallTraceId: null,
          deepLinkedCallTraceId: null,
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
      refreshOverview: async () => {
        if (get().overviewLoading) return;
        const request = ++latestOverviewRequest;
        const { dateRange } = get();
        set({ overviewLoading: true, overviewError: null });
        try {
          const overview = await api.fetchOverview(dateRange);
          if (request !== latestOverviewRequest) return;
          set({ overview, overviewLoading: false });
        } catch (error) {
          if (request !== latestOverviewRequest) return;
          set({
            overviewLoading: false,
            overviewError:
              error instanceof Error ? error.message : "统计总览读取失败",
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
      refreshMemory: async () => {
        const request = ++latestMemoryRequest;
        const { dateRange } = get();
        set({ memoryLoading: true, memoryError: null });
        try {
          const memory = await api.fetchMemory(dateRange);
          if (request !== latestMemoryRequest) return;
          set({ memory, memoryLoading: false });
        } catch (error) {
          if (request !== latestMemoryRequest) return;
          set({
            memoryLoading: false,
            memoryError:
              error instanceof Error
                ? error.message
                : "Memory 统计数据读取失败",
          });
        }
      },
      refreshRuntime: async () => {
        if (get().runtimeLoading) return;
        const request = ++latestRuntimeRequest;
        const { dateRange } = get();
        set({ runtimeLoading: true, runtimeError: null });
        try {
          const runtime = await api.fetchRuntime(dateRange);
          if (request !== latestRuntimeRequest) return;
          set({ runtime, runtimeLoading: false });
        } catch (error) {
          if (request !== latestRuntimeRequest) return;
          set({
            runtimeLoading: false,
            runtimeError:
              error instanceof Error ? error.message : "运行统计数据读取失败",
          });
        }
      },
      setCallFilters: (callFilters) => {
        latestCallsRequest += 1;
        latestCallDetailRequest += 1;
        set({
          callFilters,
          calls: null,
          callDetail: null,
          callsLoading: false,
          callsLoadingMore: false,
          callsError: null,
          callDetailLoading: false,
          callDetailError: null,
          selectedCallSpanId: null,
          selectedCallTraceId: null,
          deepLinkedCallTraceId: null,
        });
      },
      refreshCalls: async () => {
        const request = ++latestCallsRequest;
        const { dateRange, callFilters } = get();
        set({ callsLoading: true, callsError: null });
        try {
          const calls = await api.fetchCalls(dateRange, callFilters);
          if (request !== latestCallsRequest) return;
          const deepLinkedTraceId = get().deepLinkedCallTraceId;
          const first = calls.items[0] ?? null;
          const deepLinkedRow = deepLinkedTraceId
            ? (calls.items.find(
                (item) => item.trace_id === deepLinkedTraceId,
              ) ?? null)
            : null;
          set({
            calls,
            callsLoading: false,
            selectedCallSpanId: deepLinkedRow?.span_id ?? null,
            selectedCallTraceId: deepLinkedTraceId ?? first?.trace_id ?? null,
            callDetail: null,
            callDetailError: null,
          });
          if (deepLinkedTraceId) {
            await loadCallDetail(
              deepLinkedTraceId,
              deepLinkedRow?.span_id ?? null,
            );
          } else if (first) {
            await get().selectCall(first);
          }
        } catch (error) {
          if (request !== latestCallsRequest) return;
          set({
            callsLoading: false,
            callsError:
              error instanceof Error ? error.message : "调用列表读取失败",
          });
        }
      },
      loadMoreCalls: async () => {
        const current = get().calls;
        if (!current?.next_cursor || get().callsLoadingMore) return;
        const request = ++latestCallsRequest;
        const { dateRange, callFilters } = get();
        set({ callsLoadingMore: true, callsError: null });
        try {
          const nextPage = await api.fetchCalls(
            dateRange,
            callFilters,
            current.next_cursor,
          );
          if (request !== latestCallsRequest) return;
          const knownSpanIds = new Set(
            current.items.map((item) => item.span_id),
          );
          const appended = nextPage.items.filter(
            (item) => !knownSpanIds.has(item.span_id),
          );
          const items = [...current.items, ...appended];
          set({
            calls: {
              ...nextPage,
              sample_size: items.length,
              quality: combineCallQuality(items),
              freshness: mergeFreshness(current.freshness, nextPage.freshness),
              items,
            },
            callsLoadingMore: false,
          });
        } catch (error) {
          if (request !== latestCallsRequest) return;
          set({
            callsLoadingMore: false,
            callsError:
              error instanceof Error ? error.message : "更多调用读取失败",
          });
        }
      },
      selectCall: async (call) => {
        set({ deepLinkedCallTraceId: null });
        await loadCallDetail(call.trace_id, call.span_id);
      },
      openCallTrace: async (traceId) => {
        set({ deepLinkedCallTraceId: traceId });
        await loadCallDetail(traceId, null);
      },
    };
  });
}

const CALL_QUALITY_ORDER = {
  unavailable: 0,
  partial: 1,
  reliable: 2,
} as const;

/** 合并全部已加载调用的实际质量，空列表才是 unavailable。 */
function combineCallQuality(
  items: readonly CallListItem[],
): CallList["quality"] {
  if (items.length === 0) return "unavailable" as const;
  return items.reduce(
    (quality, item) =>
      CALL_QUALITY_ORDER[item.quality] < CALL_QUALITY_ORDER[quality]
        ? item.quality
        : quality,
    "reliable" as CallList["quality"],
  );
}

/** 分页读取更早数据时保留每个来源较新的新鲜度水位。 */
function mergeFreshness(
  current: CallList["freshness"],
  incoming: CallList["freshness"],
): CallList["freshness"] {
  const result = { ...current };
  for (const [source, candidate] of Object.entries(incoming)) {
    const existing = result[source];
    if (
      existing === undefined ||
      existing.updated_at === null ||
      (candidate.updated_at !== null &&
        candidate.updated_at > existing.updated_at)
    ) {
      result[source] = candidate;
    }
  }
  return result;
}

export const statisticsStore = createStatisticsStore();

/** 生成以本地今天为起止日的初始查询范围。 */
function initialDateRange(now: Date = new Date()): StatisticsDateRange {
  const date = localIsoDate(now);
  return {
    startDate: date,
    endDate: date,
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
  };
}

/** 把 Date 格式化为不受 UTC 偏移影响的本地 YYYY-MM-DD。 */
function localIsoDate(value: Date): string {
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, "0");
  const day = String(value.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}
