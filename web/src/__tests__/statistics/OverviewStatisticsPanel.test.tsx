/** 验证总览纯展示保留缺失值、来源和可复制会话问题。 */

import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { OverviewStatisticsPanel } from "../../statistics/ui/OverviewStatisticsPanel";
import { overviewStatisticsFixture } from "./overviewStatisticsFixture";

it("shows the selected-window facts and keeps unavailable trend points visible", () => {
  render(
    <OverviewStatisticsPanel
      data={overviewStatisticsFixture}
      loading={false}
      error={null}
      onCopyProblem={vi.fn()}
    />,
  );

  expect(screen.getByText("用户 SESSION")).toBeVisible();
  expect(screen.getByText("每日 token 用量")).toBeVisible();
  expect(screen.getByText("运行状态与采集缺口")).toBeVisible();
  expect(screen.getByText("Memory 使用漏斗")).toBeVisible();
  expect(screen.getByText("最近会话问题")).toBeVisible();
  expect(screen.getByText("6 / 13 ≈ 46.2%")).toBeVisible();
  expect(screen.getByText("不可用", { selector: "li span" })).toBeVisible();
  expect(screen.getByText("1 个用户 session 被中断")).toBeVisible();
  expect(screen.getByText("agent-a")).toBeVisible();
});

it("copies the problem together with its Trowel session and runtime", async () => {
  const onCopyProblem = vi.fn();
  render(
    <OverviewStatisticsPanel
      data={overviewStatisticsFixture}
      loading={false}
      error={null}
      onCopyProblem={onCopyProblem}
    />,
  );

  fireEvent.click(screen.getByRole("button", { name: "复制问题与会话信息" }));

  expect(onCopyProblem).toHaveBeenCalledWith(
    overviewStatisticsFixture.session_problems.items[0],
  );
});

it("does not render zeros when the overview source is unavailable", () => {
  render(
    <OverviewStatisticsPanel
      data={null}
      loading={false}
      error={null}
      onCopyProblem={vi.fn()}
    />,
  );

  expect(screen.getByText("统计总览暂不可用")).toBeVisible();
  expect(screen.queryByText("0", { exact: true })).toBeNull();
});

it("hides placeholder counts inside individually unavailable sources", () => {
  render(
    <OverviewStatisticsPanel
      data={{
        ...overviewStatisticsFixture,
        agent: {
          ...overviewStatisticsFixture.agent,
          quality: "unavailable",
        },
        memory: {
          ...overviewStatisticsFixture.memory,
          quality: "unavailable",
        },
        session_problems: {
          ...overviewStatisticsFixture.session_problems,
          reviewed_session_count: 0,
          problem_count: 0,
          quality: "unavailable",
          items: [],
        },
      }}
      loading={false}
      error={null}
      onCopyProblem={vi.fn()}
    />,
  );

  expect(screen.getByText("会话终态来源不可用")).toBeVisible();
  expect(screen.queryByText(/0 完成/)).toBeNull();
  expect(screen.queryByText("0 条非空问题")).toBeNull();
  expect(screen.getAllByText("来源不可用").length).toBeGreaterThan(0);
  expect(screen.getByText("n=不可用")).toBeVisible();
  expect(screen.getByText(/判断覆盖不可用/)).toBeVisible();
});

it("explains an entirely unavailable trend instead of rendering an empty chart", () => {
  render(
    <OverviewStatisticsPanel
      data={{
        ...overviewStatisticsFixture,
        token_trend: overviewStatisticsFixture.token_trend.map((point) => ({
          ...point,
          token_total: null,
          quality: "unavailable" as const,
        })),
      }}
      loading={false}
      error={null}
      onCopyProblem={vi.fn()}
    />,
  );

  expect(screen.getByText("所选时间范围 token 水位均不可用")).toBeVisible();
  expect(
    screen.queryByRole("img", { name: "所选时间范围可归因 token 用量趋势图" }),
  ).toBeNull();
});

it("distinguishes initial loading from a transport error", () => {
  const { rerender } = render(
    <OverviewStatisticsPanel
      data={null}
      loading
      error={null}
      onCopyProblem={vi.fn()}
    />,
  );
  expect(screen.getByText("正在读取统计总览…")).toBeVisible();

  rerender(
    <OverviewStatisticsPanel
      data={null}
      loading={false}
      error="后台暂时离线"
      onCopyProblem={vi.fn()}
    />,
  );
  expect(screen.getByText("后台暂时离线")).toBeVisible();
});
