import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { StatusBar } from "../components/cc/StatusBar";
import { INITIAL_REDUCER_STATE } from "../stores/ccStore";


describe("StatusBar background activity", () => {
  it("labels a pending background task as waiting, not generating", () => {
    render(
      <StatusBar
        phase="background_waiting"
        meta={INITIAL_REDUCER_STATE.meta}
        streaming={true}
        onInterrupt={() => {}}
      />,
    );

    expect(screen.getByText("等待后台任务")).toBeInTheDocument();
    expect(screen.queryByText("生成中")).toBeNull();
  });
});
