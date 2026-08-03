/** 通过统一平台 transport 读取 Statistics API。 */

import { transportFetch } from "../../platform/transport";
import type {
  ApiEnvelope,
  StatisticsDateRange,
  StatisticsResolution,
  TelemetryStatistics,
} from "../domain/types";

export async function fetchTelemetryStatistics(
  range: StatisticsDateRange,
  resolution: StatisticsResolution,
): Promise<TelemetryStatistics> {
  const query = new URLSearchParams({
    start_date: range.startDate,
    end_date: range.endDate,
    timezone: range.timezone,
    resolution,
  });
  const response = await transportFetch(
    `/api/statistics/telemetry?${query.toString()}`,
  );
  const envelope = (await response.json()) as ApiEnvelope<TelemetryStatistics>;
  if (!response.ok || !envelope.success || envelope.data === null) {
    throw new Error(envelope.error ?? `Statistics request failed: ${response.status}`);
  }
  return envelope.data;
}
