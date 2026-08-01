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
});
