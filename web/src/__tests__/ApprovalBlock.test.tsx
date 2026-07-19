import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ApprovalBlock } from "../components/cc/ApprovalBlock";
import type { ApprovalItem } from "../stores/ccStore";

const pending: ApprovalItem = {
  kind: "approval",
  requestId: "7-0",
  turnId: "turn-1",
  itemId: "exec-1",
  approvalKind: "command_approval",
  command: "/bin/zsh -lc \"printf PENDING\"",
  cwd: "/tmp/workspace",
  reason: "Allow the shell command outside the workspace?",
  availableDecisions: [
    "accept",
    {
      acceptWithExecpolicyAmendment: {
        execpolicy_amendment: ["/bin/zsh", "-lc", "printf PENDING"],
      },
    },
    "cancel",
  ],
  status: "pending",
  decision: null,
  autoResolved: false,
  resolutionReason: null,
};

describe("ApprovalBlock", () => {
  it("renders only the decisions advertised by the real request", () => {
    const onDecision = vi.fn();
    render(<ApprovalBlock item={pending} onDecision={onDecision} />);

    fireEvent.click(screen.getByRole("button", { name: "仅这次允许" }));
    fireEvent.click(screen.getByRole("button", { name: "允许同类命令" }));
    fireEvent.click(screen.getByRole("button", { name: "取消本轮" }));

    expect(onDecision.mock.calls).toEqual([
      ["7-0", "accept"],
      ["7-0", "acceptWithExecpolicyAmendment"],
      ["7-0", "cancel"],
    ]);
    expect(screen.queryByRole("button", { name: /拒绝/ })).toBeNull();
  });

  it("renders auto-declined file requests as read-only", () => {
    render(
      <ApprovalBlock
        item={{
          ...pending,
          approvalKind: "file_approval",
          command: null,
          cwd: null,
          reason: null,
          availableDecisions: [],
          status: "answered",
          decision: "decline",
          autoResolved: true,
          resolutionReason: "request omitted path, diff, and choices",
        }}
      />,
    );
    expect(screen.getByText(/已自动安全拒绝/)).toBeInTheDocument();
    expect(screen.queryAllByRole("button")).toHaveLength(0);
  });

  it.each(["expired", "host_closed"] as const)(
    "renders %s as a disabled terminal card",
    (status) => {
      render(<ApprovalBlock item={{ ...pending, status }} />);
      expect(screen.getByText(status === "expired" ? /已过期/ : /Host 已关闭/))
        .toBeInTheDocument();
      expect(screen.queryAllByRole("button")).toHaveLength(0);
    },
  );
});
