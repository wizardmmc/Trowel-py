/** 验证 Agent 统计生产容器会订阅 store 并按日期自动刷新。 */

import { render, screen, waitFor } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import { createStatisticsStore } from "../../statistics/application/store";
import { AgentStatisticsView } from "../../statistics/ui/AgentStatisticsView";
import { agentStatisticsFixture } from "./agentStatisticsFixture";

test("mounting and date changes load the matching window", async () => {
  const fetchAgent = vi.fn().mockResolvedValue(agentStatisticsFixture);
  const store = createStatisticsStore({ fetchAgent });
  render(<AgentStatisticsView store={store} />);

  await screen.findByText("用户 SESSION");
  expect(fetchAgent).toHaveBeenCalledTimes(1);

  store.getState().setDateRange({
    startDate: "2026-08-02",
    endDate: "2026-08-03",
    timezone: "Asia/Shanghai",
  });

  await waitFor(() => expect(fetchAgent).toHaveBeenCalledTimes(2));
  expect(fetchAgent).toHaveBeenLastCalledWith({
    startDate: "2026-08-02",
    endDate: "2026-08-03",
    timezone: "Asia/Shanghai",
  });
});
