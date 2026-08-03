/** 验证 Memory 统计纯展示保留各自分母、未知样本和来源质量。 */

import { render, screen } from "@testing-library/react";
import { expect, it } from "vitest";
import { MemoryStatisticsPanel } from "../../statistics/ui/MemoryStatisticsPanel";
import { memoryStatisticsFixture } from "./memoryStatisticsFixture";

it("renders the four facts, funnel, effects, assets and sources", () => {
  render(
    <MemoryStatisticsPanel
      data={memoryStatisticsFixture}
      loading={false}
      error={null}
    />,
  );

  expect(screen.getByText("归因覆盖")).toBeInTheDocument();
  expect(screen.getAllByText("5 / 47").length).toBeGreaterThan(0);
  expect(screen.getAllByText("6 / 13").length).toBeGreaterThan(0);
  expect(screen.getAllByText("6 / 147").length).toBeGreaterThan(0);
  expect(screen.getByText("unknown")).toBeInTheDocument();
  expect(screen.getByText("1 个单独样本")).toBeInTheDocument();
  expect(screen.getByText("1,799")).toBeInTheDocument();
  expect(screen.getByText("需要重建")).toBeInTheDocument();
  expect(screen.getByText("来源更新")).toBeInTheDocument();
  expect(screen.getAllByText("部分数据").length).toBeGreaterThan(0);
  expect(screen.getByRole("button", { name: "解释命中后读取" })).toHaveAttribute(
    "aria-describedby",
  );
});

it("shows unavailable without turning missing data into zero", () => {
  render(<MemoryStatisticsPanel data={null} loading={false} error={null} />);

  expect(screen.getByText("Memory 统计数据不可用")).toBeInTheDocument();
  expect(screen.queryByText("0% 命中率")).not.toBeInTheDocument();
});
