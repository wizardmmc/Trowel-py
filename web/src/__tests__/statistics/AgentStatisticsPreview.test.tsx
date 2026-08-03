/** 验证开发预览页无需后端即可展示脱敏的双 runtime 统计样例。 */

import { render, screen } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import { AgentStatisticsPreview } from "../../development/agent-statistics/AgentStatisticsPreview";

test("renders the Agent statistics page shell with representative data", async () => {
  const fetchSpy = vi.spyOn(globalThis, "fetch");
  render(<AgentStatisticsPreview />);

  expect(screen.getByRole("heading", { name: "统计" })).toBeInTheDocument();
  expect(screen.getByText("脱敏样例")).toBeInTheDocument();
  expect(screen.getByText("Agent")).toHaveAttribute("aria-current", "page");
  expect(await screen.findByText("25.9万")).toBeInTheDocument();
  expect(screen.getAllByText("glm-5.2").length).toBeGreaterThan(0);
  expect(screen.getAllByText("gpt-5.6-sol").length).toBeGreaterThan(0);
  expect(screen.getAllByText("已中断")).toHaveLength(2);
  expect(fetchSpy).not.toHaveBeenCalled();
  fetchSpy.mockRestore();
});
