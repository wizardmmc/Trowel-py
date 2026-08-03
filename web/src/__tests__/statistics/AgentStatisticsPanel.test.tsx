/** 验证 Agent 统计纯展示的事实、筛选和缺失态。 */

import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { AgentStatisticsPanel } from "../../statistics/ui/AgentStatisticsPanel";
import { agentStatisticsFixture } from "./agentStatisticsFixture";

it("renders session facts, quality, models and recent sessions", () => {
  render(
    <AgentStatisticsPanel
      data={agentStatisticsFixture}
      loading={false}
      error={null}
      runtimeFilter="all"
      modelFilter="all"
      onRuntimeFilterChange={() => {}}
      onModelFilterChange={() => {}}
    />,
  );

  expect(screen.getByText("用户 SESSION")).toBeInTheDocument();
  expect(screen.getAllByText("gpt-5.6-sol").length).toBeGreaterThan(0);
  expect(screen.getAllByText("glm-5.2").length).toBeGreaterThan(0);
  expect(screen.getByText("trowel-session-codex")).toBeInTheDocument();
  expect(screen.getAllByText("部分数据").length).toBeGreaterThan(0);
  expect(screen.getByText("14.7 秒")).toBeInTheDocument();
});

it("delegates runtime and model filter changes", () => {
  const onRuntimeFilterChange = vi.fn();
  const onModelFilterChange = vi.fn();
  render(
    <AgentStatisticsPanel
      data={agentStatisticsFixture}
      loading={false}
      error={null}
      runtimeFilter="all"
      modelFilter="all"
      onRuntimeFilterChange={onRuntimeFilterChange}
      onModelFilterChange={onModelFilterChange}
    />,
  );

  fireEvent.change(screen.getByLabelText("Runtime 筛选"), {
    target: { value: "codex" },
  });
  fireEvent.change(screen.getByLabelText("模型筛选"), {
    target: { value: "glm-5.2" },
  });

  expect(onRuntimeFilterChange).toHaveBeenCalledWith("codex");
  expect(onModelFilterChange).toHaveBeenCalledWith("glm-5.2");
});

it("shows unavailable instead of turning missing data into zero", () => {
  render(
    <AgentStatisticsPanel
      data={null}
      loading={false}
      error={null}
      runtimeFilter="all"
      modelFilter="all"
      onRuntimeFilterChange={() => {}}
      onModelFilterChange={() => {}}
    />,
  );

  expect(screen.getByText("Agent 统计数据不可用")).toBeInTheDocument();
  expect(screen.queryByText("0 token")).not.toBeInTheDocument();
});
