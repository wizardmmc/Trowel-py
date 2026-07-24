import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

import { RevertConfirmModal } from "../components/cc/RevertConfirmModal";
import type { Turn } from "../stores/ccStore";

function turn(id: string, text: string): Turn {
  return {
    id,
    userText: text,
    items: [],
    status: "done",
    turnId: id === "t2" ? "ckpt-t2" : null,
    revertible: id === "t2",
  };
}

describe("RevertConfirmModal", () => {
  it("lists every lost turn's text", () => {
    render(
      <RevertConfirmModal
        lostTurns={[turn("t2", "改这里"), turn("t3", "再改")]}
        onConfirm={() => {}}
        onCancel={() => {}}
      />,
    );
    expect(screen.getByText("改这里", { exact: false })).toBeTruthy();
    expect(screen.getByText("再改", { exact: false })).toBeTruthy();
    expect(screen.getByText(/永久丢弃/)).toBeTruthy();
  });

  it("truncates long turn text", () => {
    render(
      <RevertConfirmModal
        lostTurns={[turn("t2", "a".repeat(80))]}
        onConfirm={() => {}}
        onCancel={() => {}}
      />,
    );
    expect(screen.getByText(/…/, { exact: false })).toBeTruthy();
  });

  it("fires onConfirm / onCancel", () => {
    const onConfirm = vi.fn();
    const onCancel = vi.fn();
    render(
      <RevertConfirmModal
        lostTurns={[turn("t2", "x")]}
        onConfirm={onConfirm}
        onCancel={onCancel}
      />,
    );
    fireEvent.click(screen.getByText("确认回滚"));
    expect(onConfirm).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByText("取消"));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });
});
