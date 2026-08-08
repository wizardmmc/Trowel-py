/** 验证研讨历史栏的搜索入口和新建操作保持独立可达。 */

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { DiscussionHistoryRail } from "../discussion/ui/DiscussionHistoryRail";

describe("DiscussionHistoryRail", () => {
  it("focuses the visible search field without opening a new discussion", () => {
    const onNew = vi.fn();
    render(
      <DiscussionHistoryRail
        discussions={[]}
        currentId={null}
        loading={false}
        onOpen={() => {}}
        onNew={onNew}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "搜索研讨" }));
    expect(screen.getByRole("textbox", { name: "搜索研讨" })).toHaveFocus();
    expect(onNew).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "新建研讨" }));
    expect(onNew).toHaveBeenCalledOnce();
  });
});
