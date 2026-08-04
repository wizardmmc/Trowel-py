/** 验证 Memory 统计纯展示保留各自分母、未知样本和来源质量。 */

import { fireEvent, render, screen } from "@testing-library/react";
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
  expect(screen.getByText("5 / 47 ≈ 10.6%")).toBeVisible();
  expect(screen.getByText("6 / 13 ≈ 46.2%")).toBeVisible();
  expect(screen.getByText("6 / 147 ≈ 4.1%")).toBeVisible();
  expect(screen.getByText("unknown")).toBeInTheDocument();
  expect(screen.getByText("1 个单独样本")).toBeInTheDocument();
  expect(screen.getByText("1,799")).toBeInTheDocument();
  expect(screen.getByText("需要重建")).toBeInTheDocument();
  expect(screen.getByText("来源更新")).toBeInTheDocument();
  expect(screen.getAllByText("部分数据").length).toBeGreaterThan(0);
  const trigger = screen.getByRole("button", { name: "解释命中后读取" });
  const fact = trigger.closest(".memory-statistics__fact");
  fireEvent.click(trigger);
  const explanation = screen.getByRole("tooltip", {
    name: "命中后读取口径",
  });
  expect(explanation).toBeVisible();
  expect(fact).not.toContainElement(explanation);
  expect(trigger).toHaveAttribute("aria-expanded", "true");
});

it("shows unavailable without turning missing data into zero", () => {
  render(<MemoryStatisticsPanel data={null} loading={false} error={null} />);

  expect(screen.getByText("Memory 统计数据不可用")).toBeInTheDocument();
  expect(screen.queryByText("0% 命中率")).not.toBeInTheDocument();
});

it("keeps counts without an approximate percent when a ratio is unavailable", () => {
  render(
    <MemoryStatisticsPanel
      data={{
        ...memoryStatisticsFixture,
        retrieval: {
          ...memoryStatisticsFixture.retrieval,
          read_rate: {
            ...memoryStatisticsFixture.retrieval.read_rate,
            quality: "unavailable",
          },
        },
      }}
      loading={false}
      error={null}
    />,
  );

  const trigger = screen.getByRole("button", { name: "解释命中后读取" });
  const fact = trigger.closest(".memory-statistics__fact");
  expect(fact).toHaveTextContent("5 / 47");
  expect(fact).not.toHaveTextContent("≈");
});
