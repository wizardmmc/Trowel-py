import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  buildSessionDiagnostic,
  type PerSessionState,
} from "../agent/application";
import { INITIAL_REDUCER_STATE } from "../agent/domain";
import { SessionBanners } from "../components/cc/SessionBanners";

function session(
  checkpointAvailable: boolean | null,
): PerSessionState {
  return {
    ...INITIAL_REDUCER_STATE,
    workdir: "/repo",
    effort: null,
    name: "repo",
    displayTitle: "repo",
    titleSource: "native",
    checkpointAvailable,
    transportError: null,
    abort: null,
    connected: true,
    resourceState: "connected",
    turnState: "idle",
    liveState: "ready",
    currentTurnId: null,
    stateGeneration: 1,
    memoryEnabled: true,
    profileEnabled: true,
    runtime: "claude_code",
    nativeSessionId: null,
    permission: null,
    capabilities: [
      "tools",
      "models",
      "effort",
      "permission",
      "question",
      "interrupt",
      "slash_commands",
      "workflow",
      "tasks",
      "subagents",
      "checkpoint",
      "revert",
      "mcp",
    ],
    codexSubagents: {},
    lastSeq: null,
    needsReplay: false,
  };
}

describe("SessionBanners", () => {
  it("merges an unknown turn and its transport problem into one notice", () => {
    render(
      <SessionBanners
        active={{
          ...session(true),
          turnState: "unknown",
          liveState: "gapped",
          transportError: "turn acceptance is unknown",
          transportProblem: {
            code: "turn_acceptance_unknown",
            message: "turn acceptance is unknown",
            operation: "turn_start",
            budgetMs: 30_000,
            status: null,
            occurredAt: "2026-08-05T00:00:00.000Z",
          },
        }}
        activeSid="s1"
      />,
    );

    expect(screen.getAllByRole("alert")).toHaveLength(1);
    expect(screen.getByText("发送结果尚未确认")).toBeInTheDocument();
    expect(screen.getByText(/请勿重发上一条消息/)).toBeInTheDocument();
    expect(screen.queryByText("Agent 操作失败")).toBeNull();
    expect(screen.queryByText(/turn_start · turn_acceptance_unknown/)).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "技术详情" }));

    expect(screen.getByText(/renderer · turn_start/)).toBeInTheDocument();
    expect(screen.getByText(/turn_acceptance_unknown/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "复制会话诊断" })).toHaveClass(
      "ui-copy-button--neutral",
    );
  });

  it("offers retry close as the primary action without coloring copy green", () => {
    const onRetryClose = vi.fn();
    render(
      <SessionBanners
        active={{
          ...session(true),
          resourceState: "needs_reconcile",
          transportError: "runtime process did not exit",
          transportProblem: {
            code: "close_needs_reconcile",
            message: "runtime process did not exit",
            operation: "session_close",
            budgetMs: 30_000,
            status: 504,
            occurredAt: "2026-08-05T00:00:00.000Z",
          },
        }}
        activeSid="s1"
        onRetryClose={onRetryClose}
      />,
    );

    expect(screen.getAllByRole("alert")).toHaveLength(1);
    expect(screen.getByText("会话关闭未完成")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重试关闭" }));
    expect(onRetryClose).toHaveBeenCalledOnce();
    expect(screen.getByRole("button", { name: "复制会话诊断" })).toHaveClass(
      "ui-copy-button--neutral",
    );
  });

  it("keeps diagnostics copyable when a state issue has no transport problem", () => {
    render(
      <SessionBanners
        active={{
          ...session(true),
          resourceState: "needs_reconcile",
        }}
        activeSid="s1"
      />,
    );

    expect(screen.getByText("会话关闭未完成")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "复制会话诊断" })).toHaveClass(
      "ui-copy-button--neutral",
    );
    expect(screen.queryByRole("button", { name: "技术详情" })).toBeNull();
  });

  it("shows copy feedback only after the diagnostic reaches the clipboard", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
    render(
      <SessionBanners
        active={{
          ...session(true),
          transportError: "history request failed",
          transportProblem: {
            code: "request_timeout",
            message: "history request failed",
            operation: "session_history",
            budgetMs: 15_000,
            status: 504,
            occurredAt: "2026-08-05T00:00:00.000Z",
          },
        }}
        activeSid="s1"
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "复制会话诊断" }));

    expect(await screen.findByText("已复制")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "复制会话诊断，已复制" }),
    ).toBeInTheDocument();
    expect(writeText).toHaveBeenCalledOnce();
  });

  it("never renders free-form backend error detail in the notice", () => {
    render(
      <SessionBanners
        active={{
          ...session(true),
          transportError:
            "session secret-sid failed in /Users/alice/private for prompt text",
          transportProblem: {
            code: "http_error",
            message:
              "session secret-sid failed in /Users/alice/private for prompt text",
            operation: "turn_start",
            budgetMs: 30_000,
            status: 409,
            occurredAt: "2026-08-05T00:00:00.000Z",
          },
        }}
        activeSid="s1"
      />,
    );

    expect(screen.getByText("Agent 操作失败")).toBeInTheDocument();
    expect(screen.getByText(/Agent 返回错误/)).toBeInTheDocument();
    expect(screen.queryByText(/secret-sid/)).toBeNull();
    expect(screen.queryByText(/Users\/alice/)).toBeNull();
    expect(screen.queryByText(/prompt text/)).toBeNull();
  });

  it("groups independent session issues instead of stacking banners", () => {
    render(
      <SessionBanners
        active={{
          ...session(null),
          liveState: "reconnecting",
          capabilities: ["tools", "checkpoint"],
        }}
        activeSid="s1"
      />,
    );

    expect(screen.getAllByRole("status")).toHaveLength(1);
    expect(screen.getByText(/会话有 3 项状态需要留意/)).toBeInTheDocument();
    expect(screen.getByText("实时连接恢复中")).toBeInTheDocument();
    expect(screen.getByText("回滚可用性尚未确认")).toBeInTheDocument();
    expect(screen.getByText("Runtime capability 信息不完整")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "复制会话诊断" })).toHaveClass(
      "ui-copy-button--neutral",
    );
  });

  it("excludes free-form backend detail from copied diagnostics", () => {
    const diagnostic = buildSessionDiagnostic(
      {
        ...session(false),
        meta: { ...session(false).meta, hostDegraded: true },
        capabilities: ["tools", "checkpoint"],
        transportProblem: {
          code: "http_error",
          message:
            "session secret-sid failed in /Users/alice/private for prompt text",
          operation: "turn_start",
          budgetMs: 30_000,
          status: 409,
          occurredAt: "2026-08-05T00:00:00.000Z",
        },
      },
      {
        visibleIssueIds: ["transport", "checkpoint-unavailable", "host"],
        missingCapabilities: ["models", "effort"],
      },
    );

    expect(diagnostic).toContain('"code": "http_error"');
    expect(diagnostic).toContain('"checkpoint_available": false');
    expect(diagnostic).toContain('"host_degraded": true');
    expect(diagnostic).toContain('"visible_issue_ids"');
    expect(diagnostic).toContain('"missing":');
    expect(diagnostic).toContain('"models"');
    expect(diagnostic).not.toContain("secret-sid");
    expect(diagnostic).not.toContain("/Users/alice/private");
    expect(diagnostic).not.toContain("prompt text");
  });

  it("does not guess why checkpoint is unavailable", () => {
    render(<SessionBanners active={session(false)} activeSid="s1" />);

    expect(screen.getByText(/当前无法创建新的回滚点/)).toBeInTheDocument();
    expect(screen.getByText(/已有回滚点仍可使用/)).toBeInTheDocument();
    expect(screen.queryByText(/不是 git 仓库/)).toBeNull();
  });

  it("makes missing historical availability explicit", () => {
    render(<SessionBanners active={session(null)} activeSid="s1" />);

    expect(screen.getByText(/回滚可用性尚未确认/)).toBeInTheDocument();
  });
});
