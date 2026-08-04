/** 验证正式统计工作区组装页签、共享日期和 trace 深链接。 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { createStatisticsStore } from "../../statistics/application/store";
import { StatisticsWorkspace } from "../../statistics/ui/StatisticsWorkspace";
import { callDetailFixture, callListFixture } from "./callStatisticsFixture";
import { memoryStatisticsFixture } from "./memoryStatisticsFixture";
import { overviewStatisticsFixture } from "./overviewStatisticsFixture";

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  vi.setSystemTime(new Date("2026-08-04T12:00:00+08:00"));
});

afterEach(() => {
  vi.useRealTimers();
  window.history.replaceState({}, "", "/");
});

it("loads only the active page and keeps one date range across all tabs", async () => {
  const fetchOverview = vi.fn().mockResolvedValue(overviewStatisticsFixture);
  const fetchMemory = vi.fn().mockResolvedValue(memoryStatisticsFixture);
  const store = createStatisticsStore({ fetchOverview, fetchMemory });
  render(<StatisticsWorkspace store={store} />);

  await screen.findByText("每日 token 用量");
  expect(fetchOverview).toHaveBeenCalledTimes(1);
  expect(fetchMemory).not.toHaveBeenCalled();

  fireEvent.click(screen.getByRole("tab", { name: "Memory" }));
  await screen.findByText("归因覆盖");
  expect(fetchMemory).toHaveBeenCalledTimes(1);

  fireEvent.click(screen.getByRole("button", { name: "选择统计日期范围" }));
  fireEvent.click(screen.getByRole("button", { name: "最近 7 个自然日" }));
  await waitFor(() => expect(fetchMemory).toHaveBeenCalledTimes(2));
  expect(store.getState().dateRange).toEqual({
    startDate: "2026-07-29",
    endDate: "2026-08-04",
    timezone: store.getState().dateRange.timezone,
  });
});

it("opens a compact date popover and applies a custom range only on confirm", async () => {
  const store = createStatisticsStore({
    fetchOverview: vi.fn().mockResolvedValue(overviewStatisticsFixture),
  });
  render(<StatisticsWorkspace store={store} />);
  await screen.findByText("每日 token 用量");

  const trigger = screen.getByRole("button", { name: "选择统计日期范围" });
  fireEvent.click(trigger);
  const dialog = screen.getByRole("dialog", { name: "选择统计日期范围" });
  expect(dialog).toBeVisible();
  expect(trigger).toHaveAttribute("aria-expanded", "true");

  fireEvent.change(screen.getByLabelText("自定义开始日期"), {
    target: { value: "2026-07-20" },
  });
  fireEvent.change(screen.getByLabelText("自定义结束日期"), {
    target: { value: "2026-08-02" },
  });
  expect(store.getState().dateRange.startDate).toBe("2026-08-04");

  fireEvent.click(screen.getByRole("button", { name: "应用自定义日期" }));
  expect(store.getState().dateRange.startDate).toBe("2026-07-20");
  expect(store.getState().dateRange.endDate).toBe("2026-08-02");
});

it("labels a completed unavailable response as unavailable instead of loading", async () => {
  const store = createStatisticsStore({
    fetchOverview: vi.fn().mockResolvedValue({
      ...overviewStatisticsFixture,
      quality: "unavailable",
    }),
  });
  render(<StatisticsWorkspace store={store} />);

  expect(await screen.findByText("数据不可用")).toBeVisible();
  expect(screen.queryByText("等待数据")).toBeNull();
});

it("supports arrow-key tab switching with roving focus", async () => {
  const store = createStatisticsStore({
    fetchOverview: vi.fn().mockResolvedValue(overviewStatisticsFixture),
    fetchAgent: vi.fn().mockResolvedValue({
      ...overviewStatisticsFixture.agent,
      generated_at: overviewStatisticsFixture.generated_at,
      window_start: overviewStatisticsFixture.window_start,
      window_end: overviewStatisticsFixture.window_end,
      timezone: overviewStatisticsFixture.timezone,
      sample_size: 0,
      freshness: {},
      model_summaries: [],
      sessions: [],
    }),
  });
  render(<StatisticsWorkspace store={store} />);
  await screen.findByText("每日 token 用量");

  const overviewTab = screen.getByRole("tab", { name: "总览" });
  overviewTab.focus();
  fireEvent.keyDown(overviewTab, { key: "ArrowRight" });

  const agentTab = screen.getByRole("tab", { name: "Agent" });
  expect(agentTab).toHaveFocus();
  expect(agentTab).toHaveAttribute("aria-selected", "true");
});

it("starts each newly selected tab at the top", async () => {
  const store = createStatisticsStore({
    fetchOverview: vi.fn().mockResolvedValue(overviewStatisticsFixture),
    fetchMemory: vi.fn().mockResolvedValue(memoryStatisticsFixture),
  });
  const { container } = render(<StatisticsWorkspace store={store} />);
  await screen.findByText("每日 token 用量");
  const panel = container.querySelector<HTMLElement>(".statistics-tabs__panel");
  if (!panel) throw new Error("statistics panel missing");
  panel.scrollTop = 120;

  fireEvent.click(screen.getByRole("tab", { name: "Memory" }));
  expect(panel.scrollTop).toBe(0);
  panel.scrollTop = 80;

  fireEvent.click(screen.getByRole("tab", { name: "总览" }));
  expect(panel.scrollTop).toBe(0);
});

it("restores the Statistics URL when a mounted workspace becomes active again", async () => {
  const store = createStatisticsStore({
    fetchOverview: vi.fn().mockResolvedValue(overviewStatisticsFixture),
  });
  const { rerender } = render(
    <StatisticsWorkspace store={store} active />,
  );
  await screen.findByText("每日 token 用量");
  await waitFor(() =>
    expect(window.location.search).toContain("statistics_tab=overview"),
  );

  rerender(<StatisticsWorkspace store={store} active={false} />);
  window.history.replaceState({}, "", "/");
  rerender(<StatisticsWorkspace store={store} active />);

  await waitFor(() => {
    const params = new URLSearchParams(window.location.search);
    expect(params.get("tool")).toBe("statistics");
    expect(params.get("statistics_tab")).toBe("overview");
  });
});

it("opens a trace carried by a statistics deep link without selecting a stale row", async () => {
  window.history.replaceState(
    {},
    "",
    `/?tool=statistics&statistics_tab=calls&trace_id=${callDetailFixture.trace_id}`,
  );
  const fetchCallDetail = vi.fn().mockResolvedValue(callDetailFixture);
  const store = createStatisticsStore({
    fetchCalls: vi.fn().mockResolvedValue(callListFixture),
    fetchCallDetail,
  });

  render(<StatisticsWorkspace store={store} />);

  await screen.findByText("调用链");
  expect(fetchCallDetail).toHaveBeenCalledWith(callDetailFixture.trace_id);
  expect(store.getState().selectedCallTraceId).toBe(callDetailFixture.trace_id);
});
