/** 验证调用详情容器按日期和筛选刷新，并清除陈旧选中态。 */

import { render, screen, waitFor } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import { createStatisticsStore } from "../../statistics/application/store";
import { CallStatisticsView } from "../../statistics/ui/CallStatisticsView";
import {
  callDetailFixture,
  callListFixture,
} from "./callStatisticsFixture";

test("loads calls and automatically opens the first trace", async () => {
  const fetchCalls = vi.fn().mockResolvedValue(callListFixture);
  const fetchCallDetail = vi.fn().mockResolvedValue(callDetailFixture);
  const store = createStatisticsStore({ fetchCalls, fetchCallDetail });

  render(<CallStatisticsView store={store} />);

  await screen.findByText("mcp.tools.call");
  expect(fetchCalls).toHaveBeenCalledTimes(1);
  await waitFor(() => expect(fetchCallDetail).toHaveBeenCalledTimes(1));
  expect(fetchCallDetail).toHaveBeenCalledWith(callListFixture.items[0].trace_id);

  store.getState().setCallFilters({
    ...store.getState().callFilters,
    component: "sqlite",
  });
  await waitFor(() => expect(fetchCalls).toHaveBeenCalledTimes(2));
  expect(store.getState().selectedCallSpanId).toBe(callListFixture.items[0].span_id);
});
