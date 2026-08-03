/** 通过统一平台 transport 读取 Statistics API。 */

import { transportFetch } from "../../platform/transport";
import type {
  ApiEnvelope,
  AgentStatistics,
  MemoryStatistics,
  RuntimeStatistics,
  StatisticsDateRange,
  StatisticsResolution,
  TelemetryStatistics,
} from "../domain/types";

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

function dateRangeQuery(range: StatisticsDateRange): URLSearchParams {
  return new URLSearchParams({
    start_date: range.startDate,
    end_date: range.endDate,
    timezone: range.timezone,
  });
}

async function fetchStatistics<T>(path: string): Promise<T> {
  const response = await transportFetch(path);
  const envelope = (await response.json()) as ApiEnvelope<T>;
  if (!response.ok || !envelope.success || envelope.data === null) {
    throw new Error(envelope.error ?? `Statistics request failed: ${response.status}`);
  }
  return envelope.data;
}
