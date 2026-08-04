/** 验证总览生产容器读取共享日期并执行会话问题复制。 */

import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { createStatisticsStore } from "../../statistics/application/store";
import { OverviewStatisticsView } from "../../statistics/ui/OverviewStatisticsView";
import { overviewStatisticsFixture } from "./overviewStatisticsFixture";

it("loads the overview and copies a provenance-safe problem bundle", async () => {
  const writeText = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText },
  });
  const fetchOverview = vi.fn().mockResolvedValue(overviewStatisticsFixture);
  const store = createStatisticsStore({ fetchOverview });
  render(<OverviewStatisticsView store={store} />);

  await screen.findByText("最近会话问题");
  fireEvent.click(screen.getByRole("button", { name: "复制问题与会话信息" }));

  expect(fetchOverview).toHaveBeenCalledWith(store.getState().dateRange);
  expect(writeText).toHaveBeenCalledWith(
    "问题：为什么这次调用出现了明显延迟？\n会话 ID：agent-a\nRuntime：codex",
  );
});
