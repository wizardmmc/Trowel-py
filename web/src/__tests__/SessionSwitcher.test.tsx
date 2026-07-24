import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { SessionSwitcher } from "../components/cc/SessionSwitcher";

const HISTORY = [
  {
    runtime: "claude_code" as const,
    native_session_id: "cc-1",
    title: "Claude title",
    updated_at: 30,
  },
  {
    runtime: "codex" as const,
    native_session_id: "codex-1",
    title: "Codex title",
    updated_at: 20,
  },
];

describe("SessionSwitcher history modal", () => {
  it("opens a centered dialog, keeps runtime columns aligned and restores focus", async () => {
    render(
      <SessionSwitcher
        history={HISTORY}
        loading={false}
        loadingMore={false}
        hasMore
        onLoadMore={() => {}}
        onPick={() => {}}
        onNew={() => {}}
      />,
    );
    const trigger = screen.getByRole("button", { name: "历史会话" });
    fireEvent.click(trigger);

    expect(screen.getByRole("dialog", { name: "历史会话" })).toBeInTheDocument();
    expect(screen.getByText("Claude")).toHaveClass("history-row__badge");
    expect(screen.getByText("Codex")).toHaveClass("history-row__badge");
    fireEvent.keyDown(screen.getByRole("listbox"), { key: "Escape" });
    await waitFor(() => expect(trigger).toHaveFocus());
  });

  it("loads more near the bottom and supports arrow + Enter selection", () => {
    const onLoadMore = vi.fn();
    const onPick = vi.fn();
    render(
      <SessionSwitcher
        history={HISTORY}
        loading={false}
        loadingMore={false}
        hasMore
        onLoadMore={onLoadMore}
        onPick={onPick}
        onNew={() => {}}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "历史会话" }));
    const list = screen.getByRole("listbox");
    Object.defineProperties(list, {
      clientHeight: { value: 200, configurable: true },
      scrollHeight: { value: 500, configurable: true },
      scrollTop: { value: 290, configurable: true },
    });
    fireEvent.scroll(list);
    expect(onLoadMore).toHaveBeenCalledTimes(1);

    fireEvent.keyDown(list, { key: "ArrowDown" });
    fireEvent.keyDown(list, { key: "Enter" });
    expect(onPick).toHaveBeenCalledWith(HISTORY[1]);
  });
});
