/** 验证调用列表筛选、键盘选择和 trace 缺口展示。 */

import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test, vi } from "vitest";
import { CallStatisticsPanel } from "../../statistics/ui/CallStatisticsPanel";
import {
  callDetailFixture,
  callListFixture,
} from "./callStatisticsFixture";

test("filters calls and selects rows with pointer or keyboard", async () => {
  const user = userEvent.setup();
  const onFiltersChange = vi.fn();
  const onSelectCall = vi.fn();
  render(
    <CallStatisticsPanel
      calls={callListFixture}
      detail={callDetailFixture}
      filters={{
        component: "all",
        operation: "all",
        runtime: "all",
        status: "all",
        minimumDurationMs: 0,
      }}
      selectedSpanId="0000000000000002"
      loading={false}
      error={null}
      detailLoading={false}
      detailError={null}
      loadingMore={false}
      onFiltersChange={onFiltersChange}
      onSelectCall={onSelectCall}
      onLoadMore={vi.fn()}
    />,
  );

  expect(screen.getByLabelText("调用组件").tagName).toBe("BUTTON");
  await user.click(screen.getByLabelText("调用组件"));
  await user.click(screen.getByRole("option", { name: "SQLite" }));
  expect(onFiltersChange).toHaveBeenCalledWith(
    expect.objectContaining({ component: "sqlite" }),
  );

  const rows = screen.getAllByRole("row").slice(1);
  expect(rows.map((row) => row.tabIndex)).toEqual([0, -1]);
  fireEvent.keyDown(rows[0], { key: "ArrowDown" });
  expect(onSelectCall).toHaveBeenCalledWith(callListFixture.items[1]);
  fireEvent.click(rows[1]);
  expect(onSelectCall).toHaveBeenCalledWith(callListFixture.items[1]);
});

test("renders real parent nesting, span links and unavailable gaps", () => {
  render(
    <CallStatisticsPanel
      calls={callListFixture}
      detail={callDetailFixture}
      filters={{
        component: "all",
        operation: "all",
        runtime: "all",
        status: "all",
        minimumDurationMs: 0,
      }}
      selectedSpanId="0000000000000002"
      loading={false}
      error={null}
      detailLoading={false}
      detailError={null}
      loadingMore={false}
      onFiltersChange={vi.fn()}
      onSelectCall={vi.fn()}
      onLoadMore={vi.fn()}
    />,
  );

  expect(
    screen.getByText("sqlite.sessions.read", { selector: "strong" }),
  ).toHaveAttribute(
    "data-depth",
    "1",
  );
  expect(screen.getByText(/关联到 00000000/)).toBeVisible();
  expect(screen.getByText(/内部调度区间未采集/)).toBeVisible();
  expect(screen.getByText(/不保存 prompt、thinking/)).toBeVisible();
});
