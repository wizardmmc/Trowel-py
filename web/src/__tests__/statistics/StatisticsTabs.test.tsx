/** 验证纯 props 统计页签壳的选择和日期事件。 */

import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { StatisticsTabs } from "../../statistics/ui/StatisticsTabs";

it("renders five stable tabs and delegates all state changes", () => {
  const onTabChange = vi.fn();
  const onDateRangeChange = vi.fn();

  render(
    <StatisticsTabs
      activeTab="overview"
      dateRange={{
        startDate: "2026-08-01",
        endDate: "2026-08-03",
        timezone: "Asia/Shanghai",
      }}
      onTabChange={onTabChange}
      onDateRangeChange={onDateRangeChange}
    >
      <div>content</div>
    </StatisticsTabs>,
  );

  expect(screen.getAllByRole("tab")).toHaveLength(5);
  fireEvent.click(screen.getByRole("tab", { name: "Memory" }));
  fireEvent.change(screen.getByLabelText("开始日期"), {
    target: { value: "2026-08-02" },
  });

  expect(onTabChange).toHaveBeenCalledWith("memory");
  expect(onDateRangeChange).toHaveBeenCalledWith({
    startDate: "2026-08-02",
    endDate: "2026-08-03",
    timezone: "Asia/Shanghai",
  });
  expect(screen.getByText("content")).toBeInTheDocument();
});
