import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { PerSessionState } from "../agent/application";
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
    memoryEnabled: true,
    profileEnabled: true,
    runtime: "claude_code",
    nativeSessionId: null,
    permission: null,
    capabilities: ["tools", "checkpoint", "revert"],
    codexSubagents: {},
    lastSeq: null,
    needsReplay: false,
  };
}

describe("SessionBanners", () => {
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
