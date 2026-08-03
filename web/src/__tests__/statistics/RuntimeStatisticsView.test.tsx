/** 验证运行统计容器按共享日期刷新并展示样本缺口。 */

import { render, screen, waitFor } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import { createStatisticsStore } from "../../statistics/application/store";
import { RuntimeStatisticsView } from "../../statistics/ui/RuntimeStatisticsView";
import { runtimeStatisticsFixture } from "./runtimeStatisticsFixture";

test("mounting and date changes load runtime statistics", async () => {
  const fetchRuntime = vi.fn().mockResolvedValue(runtimeStatisticsFixture);
  const store = createStatisticsStore({ fetchRuntime });
  render(<RuntimeStatisticsView store={store} />);

  await screen.findByText("SIDECAR 本次运行");
  expect(fetchRuntime).toHaveBeenCalledTimes(1);
  expect(screen.getByText("RSS 只有一次采样，只展示事实，不判断上涨。")).toBeVisible();
  expect(screen.getByText("SQLite busy / locked")).toBeVisible();
  expect(screen.getByText("RSS 时序")).toBeVisible();
  expect(screen.getByText(/只有一个时间桶/)).toBeVisible();
  expect(screen.getByText("sessions.db")).toBeVisible();

  store.getState().setDateRange({
    startDate: "2026-08-02",
    endDate: "2026-08-03",
    timezone: "Asia/Shanghai",
  });

  await waitFor(() => expect(fetchRuntime).toHaveBeenCalledTimes(2));
  expect(fetchRuntime).toHaveBeenLastCalledWith({
    startDate: "2026-08-02",
    endDate: "2026-08-03",
    timezone: "Asia/Shanghai",
  });
});
