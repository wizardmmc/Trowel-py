/** 验证工作区首页的历史会话入口和分页状态。 */

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { WorkspaceHome } from "../agent/ui/WorkspaceHome";

describe("WorkspaceHome", () => {
  it("uses the full Claude name for Claude Code history", () => {
    render(
      <WorkspaceHome
        workdir="/repo"
        history={[
          {
            runtime: "claude_code",
            native_session_id: "claude-session-1",
            title: "Claude 历史",
            updated_at: "2026-08-01T10:00:00+00:00",
          },
        ]}
        loadingHistory={false}
        loadingMoreHistory={false}
        historyHasMore={false}
        historyError={null}
        onNewSession={() => {}}
        onSwitchWorkspace={() => {}}
        onPickHistory={() => {}}
        onLoadMoreHistory={() => {}}
        onRetryHistory={() => {}}
      />,
    );

    expect(screen.getByText("Claude")).toBeInTheDocument();
    expect(screen.queryByText("CC")).toBeNull();
  });

  it("loads another history page and disables the action while loading", () => {
    const loadMore = vi.fn();
    const props = {
      workdir: "/repo",
      history: [
        {
          runtime: "codex" as const,
          native_session_id: "thread-1",
          title: "已有会话",
          updated_at: "2026-08-01T10:00:00+00:00",
        },
      ],
      loadingHistory: false,
      historyError: null,
      historyHasMore: true,
      loadingMoreHistory: false,
      onNewSession: vi.fn(),
      onSwitchWorkspace: vi.fn(),
      onPickHistory: vi.fn(),
      onRetryHistory: vi.fn(),
      onLoadMoreHistory: loadMore,
    };
    const { rerender } = render(<WorkspaceHome {...props} />);

    fireEvent.click(screen.getByRole("button", { name: "加载更多" }));
    expect(loadMore).toHaveBeenCalledOnce();

    rerender(<WorkspaceHome {...props} loadingMoreHistory />);
    expect(screen.getByRole("button", { name: "正在加载..." })).toBeDisabled();
  });
});
