/** 验证 statistics store 统一持有页签、日期和查询状态。 */

import { expect, it, vi } from "vitest";
import { createStatisticsStore } from "../../statistics/application/store";
import { runtimeStatisticsFixture } from "./runtimeStatisticsFixture";
import {
  callDetailFixture,
  callListFixture,
} from "./callStatisticsFixture";

it("updates shared tab and date state without fetching", () => {
  const fetchTelemetry = vi.fn();
  const store = createStatisticsStore({ fetchTelemetry });

  store.getState().setActiveTab("memory");
  store.getState().setModelFilter("old-model");
  store.getState().setDateRange({
    startDate: "2026-08-01",
    endDate: "2026-08-03",
    timezone: "Asia/Shanghai",
  });

  expect(store.getState().activeTab).toBe("memory");
  expect(store.getState().dateRange.timezone).toBe("Asia/Shanghai");
  expect(store.getState().modelFilter).toBe("all");
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

it("loads Agent statistics and keeps runtime/model filters local", async () => {
  const payload = { quality: "partial" as const, sample_size: 2 };
  const fetchAgent = vi.fn().mockResolvedValue(payload);
  const store = createStatisticsStore({ fetchAgent });

  store.getState().setRuntimeFilter("codex");
  store.getState().setModelFilter("gpt-5.6-sol");
  await store.getState().refreshAgent();

  expect(store.getState().agent).toBe(payload);
  expect(store.getState().runtimeFilter).toBe("codex");
  expect(store.getState().modelFilter).toBe("gpt-5.6-sol");
  expect(store.getState().agentLoading).toBe(false);
  expect(store.getState().agentError).toBeNull();
});

it("clears an incompatible model filter when runtime changes", () => {
  const store = createStatisticsStore();
  store.getState().setModelFilter("gpt-5.6-sol");

  store.getState().setRuntimeFilter("claude_code");

  expect(store.getState().runtimeFilter).toBe("claude_code");
  expect(store.getState().modelFilter).toBe("all");
});

it("loads Memory statistics without sharing Agent request state", async () => {
  const payload = { quality: "partial" as const, sample_size: 47 };
  const fetchMemory = vi.fn().mockResolvedValue(payload);
  const store = createStatisticsStore({ fetchMemory });

  await store.getState().refreshMemory();

  expect(store.getState().memory).toBe(payload);
  expect(store.getState().memoryLoading).toBe(false);
  expect(store.getState().memoryError).toBeNull();
  expect(store.getState().agent).toBeNull();
});

it("loads Runtime statistics without sharing other page request state", async () => {
  const fetchRuntime = vi.fn().mockResolvedValue(runtimeStatisticsFixture);
  const store = createStatisticsStore({ fetchRuntime });

  await store.getState().refreshRuntime();

  expect(store.getState().runtime).toBe(runtimeStatisticsFixture);
  expect(store.getState().runtimeLoading).toBe(false);
  expect(store.getState().runtimeError).toBeNull();
  expect(store.getState().telemetry).toBeNull();
});

it("ignores stale call detail after filters start a newer request", async () => {
  let resolveOldDetail!: (value: typeof callDetailFixture) => void;
  const oldDetail = new Promise<typeof callDetailFixture>((resolve) => {
    resolveOldDetail = resolve;
  });
  const newerDetail = {
    ...callDetailFixture,
    root_operation: "runtime.call" as const,
  };
  const fetchCalls = vi.fn().mockResolvedValue(callListFixture);
  const fetchCallDetail = vi
    .fn()
    .mockReturnValueOnce(oldDetail)
    .mockResolvedValueOnce(newerDetail);
  const store = createStatisticsStore({ fetchCalls, fetchCallDetail });

  const oldRefresh = store.getState().refreshCalls();
  await vi.waitFor(() => expect(fetchCallDetail).toHaveBeenCalledTimes(1));
  store.getState().setCallFilters({
    ...store.getState().callFilters,
    component: "mcp",
  });
  await store.getState().refreshCalls();
  resolveOldDetail(callDetailFixture);
  await oldRefresh;

  expect(store.getState().callDetail?.root_operation).toBe(
    "runtime.call",
  );
  expect(store.getState().selectedCallSpanId).toBe(
    callListFixture.items[0].span_id,
  );
});

it("appends a cursor page without duplicating spans or changing selection", async () => {
  const older = {
    ...callListFixture.items[1],
    trace_id: "00000000000000000000000000000003",
    span_id: "0000000000000004",
  };
  const nextPage = {
    ...callListFixture,
    items: [callListFixture.items[1], older],
    next_cursor: null,
    quality: "reliable" as const,
    freshness: {
      telemetry: { updated_at: "2026-08-03T11:59:00Z", status: "fresh" as const },
    },
  };
  const fetchCalls = vi
    .fn()
    .mockResolvedValueOnce(callListFixture)
    .mockResolvedValueOnce(nextPage);
  const store = createStatisticsStore({
    fetchCalls,
    fetchCallDetail: vi.fn().mockResolvedValue(callDetailFixture),
  });

  await store.getState().refreshCalls();
  await store.getState().loadMoreCalls();

  expect(store.getState().calls?.items.map((item) => item.span_id)).toEqual([
    "0000000000000002",
    "0000000000000001",
    "0000000000000004",
  ]);
  expect(fetchCalls).toHaveBeenLastCalledWith(
    store.getState().dateRange,
    store.getState().callFilters,
    "next-page",
  );
  expect(store.getState().selectedCallSpanId).toBe("0000000000000002");
  expect(store.getState().calls?.quality).toBe("partial");
  expect(store.getState().calls?.freshness.telemetry.updated_at).toBe(
    "2026-08-03T12:00:01Z",
  );
});
