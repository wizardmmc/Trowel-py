/** 验证 Memory 统计生产容器会按共享日期自动刷新。 */

import { render, screen, waitFor } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import { createStatisticsStore } from "../../statistics/application/store";
import { MemoryStatisticsView } from "../../statistics/ui/MemoryStatisticsView";
import { memoryStatisticsFixture } from "./memoryStatisticsFixture";

test("mounting and date changes load the matching Memory window", async () => {
  const fetchMemory = vi.fn().mockResolvedValue(memoryStatisticsFixture);
  const store = createStatisticsStore({ fetchMemory });
  render(<MemoryStatisticsView store={store} />);

  await screen.findByText("归因覆盖");
  expect(fetchMemory).toHaveBeenCalledTimes(1);

  store.getState().setDateRange({
    startDate: "2026-08-01",
    endDate: "2026-08-02",
    timezone: "Asia/Shanghai",
  });

  await waitFor(() => expect(fetchMemory).toHaveBeenCalledTimes(2));
  expect(fetchMemory).toHaveBeenLastCalledWith({
    startDate: "2026-08-01",
    endDate: "2026-08-02",
    timezone: "Asia/Shanghai",
  });
});
