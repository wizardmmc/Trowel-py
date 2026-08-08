/** 验证统一确认弹窗不依赖浏览器原生 confirm。 */

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ConfirmDialog } from "../components/ui/ConfirmDialog";

describe("ConfirmDialog", () => {
  it("通过 Trowel 对话框确认危险操作", () => {
    const onConfirm = vi.fn();
    render(
      <ConfirmDialog
        title="删除这场研讨？"
        description="公开记录将不再出现。"
        confirmLabel="删除研讨"
        tone="danger"
        onConfirm={onConfirm}
        onCancel={() => {}}
      />,
    );

    expect(screen.getByRole("alertdialog")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "删除研讨" }));
    expect(onConfirm).toHaveBeenCalledOnce();
  });

  it("按 Escape 取消", () => {
    const onCancel = vi.fn();
    render(
      <ConfirmDialog
        title="确认操作？"
        description="操作说明。"
        confirmLabel="确认"
        onConfirm={() => {}}
        onCancel={onCancel}
      />,
    );

    fireEvent.keyDown(window, { key: "Escape" });
    expect(onCancel).toHaveBeenCalledOnce();
  });
});
