import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { StatusBar } from "../components/cc/StatusBar";
import { INITIAL_REDUCER_STATE } from "../agent";


describe("StatusBar background activity", () => {
  it("labels a pending background task as waiting, not generating", () => {
    render(
      <StatusBar
        phase="background_waiting"
        meta={INITIAL_REDUCER_STATE.meta}
        runtimeLabel="Codex"
        streaming={true}
        onInterrupt={() => {}}
      />,
    );

    expect(screen.getByText("等待后台任务")).toBeInTheDocument();
    expect(screen.queryByText("生成中")).toBeNull();
  });

  it("uses the presentation label while waiting for the runtime", () => {
    render(
      <StatusBar
        phase="awaiting_first"
        meta={INITIAL_REDUCER_STATE.meta}
        runtimeLabel="Codex"
        streaming={true}
      />,
    );

    expect(screen.getByText("等待 Codex 接手…")).toBeInTheDocument();
    expect(screen.queryByText(/等待 CC 接手/)).toBeNull();
  });

  it("shows normalized turn tokens and completed compaction count without cost or num_turns", () => {
    render(
      <StatusBar
        phase="done"
        meta={{
          ...INITIAL_REDUCER_STATE.meta,
          costUsd: 0.0421,
          numTurns: 7,
          lastTurnTokens: 12_405,
          compactionCount: 2,
        }}
        runtimeLabel="Claude"
        streaming={false}
      />,
    );

    expect(screen.getByText("本轮 12.4k tokens · 已压缩 2 次")).toBeInTheDocument();
    expect(screen.queryByText(/\$0\.0421/)).toBeNull();
    expect(screen.queryByText(/7 轮/)).toBeNull();
  });

  it("uses a Chinese status while automatic compaction is running", () => {
    render(
      <StatusBar
        phase="compacting"
        meta={{
          ...INITIAL_REDUCER_STATE.meta,
          lastTurnTokens: null,
          compactionCount: 0,
        }}
        runtimeLabel="Codex"
        streaming={true}
      />,
    );

    expect(screen.getByText("正在自动压缩")).toBeInTheDocument();
  });
});
