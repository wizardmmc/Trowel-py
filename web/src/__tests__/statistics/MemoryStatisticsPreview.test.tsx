/** 验证开发预览页无需后端即可展示 Memory 统计样例。 */

import { render, screen } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import { MemoryStatisticsPreview } from "../../development/memory-statistics/MemoryStatisticsPreview";

test("renders the Memory statistics page shell with representative data", async () => {
  const fetchSpy = vi.spyOn(globalThis, "fetch");
  render(<MemoryStatisticsPreview />);

  expect(screen.getByRole("heading", { name: "统计" })).toBeInTheDocument();
  expect(screen.getByText("脱敏样例")).toBeInTheDocument();
  expect(screen.getByText("Memory")).toHaveAttribute("aria-current", "page");
  expect(await screen.findByText("83.19%")).toBeInTheDocument();
  expect(screen.getByText("5 / 47 ≈ 10.6%")).toBeVisible();
  expect(screen.getByText("从找到到生效")).toBeInTheDocument();
  expect(fetchSpy).not.toHaveBeenCalled();
  fetchSpy.mockRestore();
});
