/** 验证 statistics store 统一持有页签、日期和查询状态。 */

import { expect, it, vi } from "vitest";
import { createStatisticsStore } from "../statistics/application/store";

it("updates shared tab and date state without fetching", () => {
  const fetchTelemetry = vi.fn();
  const store = createStatisticsStore({ fetchTelemetry });

  store.getState().setActiveTab("memory");
  store.getState().setDateRange({
    startDate: "2026-08-01",
    endDate: "2026-08-03",
    timezone: "Asia/Shanghai",
  });

  expect(store.getState().activeTab).toBe("memory");
  expect(store.getState().dateRange.timezone).toBe("Asia/Shanghai");
  expect(fetchTelemetry).not.toHaveBeenCalled();
});

it("loads telemetry through the injected transport and keeps quality", async () => {
  const payload = {
    quality: "partial" as const,
    sample_size: 3,
  };
  const fetchTelemetry = vi.fn().mockResolvedValue(payload);
  const store = createStatisticsStore({ fetchTelemetry });

  await store.getState().refreshTelemetry();

  expect(store.getState().telemetry).toBe(payload);
  expect(store.getState().loading).toBe(false);
  expect(store.getState().error).toBeNull();
});

it("keeps the last data but exposes refresh failures", async () => {
  const fetchTelemetry = vi
    .fn()
    .mockResolvedValueOnce({ quality: "reliable", sample_size: 5 })
    .mockRejectedValueOnce(new Error("offline"));
  const store = createStatisticsStore({ fetchTelemetry });
  await store.getState().refreshTelemetry();

  await store.getState().refreshTelemetry();

  expect(store.getState().telemetry?.sample_size).toBe(5);
  expect(store.getState().error).toBe("offline");
});

it("ignores an older response after the date range changes", async () => {
  let resolveOld: (value: { quality: "reliable"; sample_size: number }) => void;
  const oldResponse = new Promise<{ quality: "reliable"; sample_size: number }>(
    (resolve) => {
      resolveOld = resolve;
    },
  );
  const fetchTelemetry = vi
    .fn()
    .mockReturnValueOnce(oldResponse)
    .mockResolvedValueOnce({ quality: "reliable", sample_size: 9 });
  const store = createStatisticsStore({ fetchTelemetry });

  const oldRefresh = store.getState().refreshTelemetry();
  store.getState().setDateRange({
    startDate: "2026-08-02",
    endDate: "2026-08-03",
    timezone: "Asia/Shanghai",
  });
  await store.getState().refreshTelemetry();
  resolveOld!({ quality: "reliable", sample_size: 1 });
  await oldRefresh;

  expect(store.getState().telemetry?.sample_size).toBe(9);
  expect(store.getState().loading).toBe(false);
});
