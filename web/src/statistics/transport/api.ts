/** 通过统一平台 transport 读取 Statistics API。 */

import { transportFetch } from "../../platform/transport";
import type {
  ApiEnvelope,
  AgentStatistics,
  CallDetail,
  CallFilters,
  CallList,
  MemoryStatistics,
  RuntimeStatistics,
  StatisticsDateRange,
  StatisticsResolution,
  TelemetryStatistics,
} from "../domain/types";
import type { OverviewStatistics } from "../domain/overview";

export async function fetchOverviewStatistics(
  range: StatisticsDateRange,
): Promise<OverviewStatistics> {
  const query = dateRangeQuery(range);
  return fetchStatistics<OverviewStatistics>(
    `/api/statistics/overview?${query.toString()}`,
  );
}

export async function fetchAgentStatistics(
  range: StatisticsDateRange,
): Promise<AgentStatistics> {
  const query = dateRangeQuery(range);
  return fetchStatistics<AgentStatistics>(
    `/api/statistics/agent?${query.toString()}`,
  );
}

export async function fetchMemoryStatistics(
  range: StatisticsDateRange,
): Promise<MemoryStatistics> {
  const query = dateRangeQuery(range);
  return fetchStatistics<MemoryStatistics>(
    `/api/statistics/memory?${query.toString()}`,
  );
}

export async function fetchTelemetryStatistics(
  range: StatisticsDateRange,
  resolution: StatisticsResolution,
): Promise<TelemetryStatistics> {
  const query = dateRangeQuery(range);
  query.set("resolution", resolution);
  return fetchStatistics<TelemetryStatistics>(
    `/api/statistics/telemetry?${query.toString()}`,
  );
}

export async function fetchRuntimeStatistics(
  range: StatisticsDateRange,
): Promise<RuntimeStatistics> {
  const query = dateRangeQuery(range);
  return fetchStatistics<RuntimeStatistics>(
    `/api/statistics/runtime?${query.toString()}`,
  );
}

/** 按日期、受控筛选和可选游标读取一页调用。 */
export async function fetchCallStatistics(
  range: StatisticsDateRange,
  filters: CallFilters,
  cursor?: string,
): Promise<CallList> {
  const query = dateRangeQuery(range);
  setOptionalFilter(query, "component", filters.component);
  setOptionalFilter(query, "operation", filters.operation);
  setOptionalFilter(query, "runtime", filters.runtime);
  setOptionalFilter(query, "status", filters.status);
  if (filters.minimumDurationMs > 0) {
    query.set("minimum_duration_ms", String(filters.minimumDurationMs));
  }
  if (cursor) query.set("cursor", cursor);
  return fetchStatistics<CallList>(`/api/statistics/calls?${query.toString()}`);
}

/** 按随机 trace 身份读取跨 link 可达的有限调用详情。 */
export async function fetchCallDetail(traceId: string): Promise<CallDetail> {
  return fetchStatistics<CallDetail>(
    `/api/statistics/calls/${encodeURIComponent(traceId)}`,
  );
}

/** 把本地日期范围转换为所有 Statistics API 共用的查询参数。 */
function dateRangeQuery(range: StatisticsDateRange): URLSearchParams {
  return new URLSearchParams({
    start_date: range.startDate,
    end_date: range.endDate,
    timezone: range.timezone,
  });
}

/** 仅在筛选值不是“全部”时写入服务端查询参数。 */
function setOptionalFilter(
  query: URLSearchParams,
  name: string,
  value: string,
): void {
  if (value !== "all") query.set(name, value);
}

/** 请求 Statistics envelope，并把公开错误转换为前端异常。 */
async function fetchStatistics<T>(path: string): Promise<T> {
  const response = await transportFetch(path);
  const envelope = (await response.json()) as ApiEnvelope<T>;
  if (!response.ok || !envelope.success || envelope.data === null) {
    throw new Error(
      envelope.error ?? `Statistics request failed: ${response.status}`,
    );
  }
  return envelope.data;
}
