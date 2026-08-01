/** 验证统一工作区容器不会混淆普通切换与换目录新开。 */

import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { RecentWorkspace } from "../agent/application/workdirs";
import { useAgentStore } from "../agent/application/store";

const mocks = vi.hoisted(() => ({
  listRecentWorkspaces: vi.fn<
    () => Promise<readonly RecentWorkspace[]>
  >(),
  rememberRecentWorkspace: vi.fn<
    (path: string) => Promise<RecentWorkspace>
  >(),
  listWorkdirEntries: vi.fn().mockResolvedValue([]),
  selectWorkdir: vi.fn<() => Promise<string | null>>(),
  platform: { environment: "browser" as "browser" | "desktop" },
}));

vi.mock("../agent/application/workdirs", () => ({
  listRecentWorkspaces: mocks.listRecentWorkspaces,
  rememberRecentWorkspace: mocks.rememberRecentWorkspace,
  listWorkdirEntries: mocks.listWorkdirEntries,
}));

vi.mock("../platform", () => ({
  getPlatform: () => ({
    environment: mocks.platform.environment,
    selectWorkdir: mocks.selectWorkdir,
  }),
}));

vi.mock("../agent/ui/SessionView", () => ({
  SessionView: (props: {
    readonly workdir: string;
    readonly emptyWorkspaceContent?: ReactNode;
    readonly newSessionWorkdirRequest?: {
      readonly id: number;
      readonly workdir: string;
    } | null;
    readonly onRequestChangeWorkdir?: () => void;
    readonly onRequestNewWorkdir?: () => void;
  }) => (
    <div>
      <output aria-label="当前 renderer 工作区">{props.workdir}</output>
      {props.emptyWorkspaceContent}
      {props.newSessionWorkdirRequest && (
        <output aria-label="新会话工作区">
          {props.newSessionWorkdirRequest.workdir}
        </output>
      )}
      <button type="button" onClick={props.onRequestChangeWorkdir}>
        切换工作区入口
      </button>
      <button type="button" onClick={props.onRequestNewWorkdir}>
        换目录新开入口
      </button>
    </div>
  ),
}));

import { AgentWorkspace } from "../agent/ui/AgentWorkspace";

const RECENT: RecentWorkspace = {
  path: "/repo",
  name: "repo",
  lastOpenedAt: new Date().toISOString(),
  available: true,
};

/** 创建由测试显式结束的异步操作。 */
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

describe("AgentWorkspace", () => {
  beforeEach(() => {
    mocks.platform.environment = "browser";
    mocks.listRecentWorkspaces.mockReset().mockResolvedValue([RECENT]);
    mocks.rememberRecentWorkspace.mockReset().mockImplementation(async (path) => ({
      ...RECENT,
      path,
      name: path.split("/").pop() || path,
    }));
    mocks.listWorkdirEntries.mockClear();
    mocks.selectWorkdir.mockReset().mockResolvedValue(null);
    useAgentStore.setState({ sessions: {}, activeSid: null });
  });

  it("opens a Recent workspace without requesting a new session", async () => {
    render(<AgentWorkspace />);
    fireEvent.click(await screen.findByRole("button", { name: /repo/ }));

    await waitFor(() => {
      expect(screen.getByLabelText("当前 renderer 工作区"))
        .toHaveTextContent("/repo");
    });
    expect(mocks.rememberRecentWorkspace).toHaveBeenCalledWith("/repo");
    expect(screen.queryByLabelText("新会话工作区")).toBeNull();
  });

  it("returns to the workspace home when switching away from a live session", async () => {
    useAgentStore.setState({ activeSid: "live-session" });
    render(<AgentWorkspace />);

    fireEvent.click(await screen.findByRole("button", { name: /repo/ }));

    await waitFor(() => {
      expect(screen.getByLabelText("当前 renderer 工作区"))
        .toHaveTextContent("/repo");
    });
    expect(useAgentStore.getState().activeSid).toBeNull();
  });

  it("turns a new-workdir selection into an explicit session request", async () => {
    render(<AgentWorkspace />);
    fireEvent.click(await screen.findByRole("button", { name: "新建 Agent 会话" }));
    const chooser = screen.getByRole("dialog", {
      name: "选择新会话的工作区",
    });
    fireEvent.click(within(chooser).getByRole("button", { name: /repo/ }));

    expect(await screen.findByLabelText("新会话工作区"))
      .toHaveTextContent("/repo");
    expect(screen.getByLabelText("当前 renderer 工作区")).toBeEmptyDOMElement();
  });

  it("uses the native folder picker only in desktop mode", async () => {
    mocks.platform.environment = "desktop";
    mocks.selectWorkdir.mockResolvedValueOnce("/native-project");
    render(<AgentWorkspace />);
    fireEvent.click(await screen.findByRole("button", { name: "打开工作区..." }));
    const chooser = screen.getByRole("dialog", { name: "选择工作区" });
    fireEvent.click(
      within(chooser).getByRole("button", { name: "打开其他文件夹..." }),
    );

    await waitFor(() => expect(mocks.selectWorkdir).toHaveBeenCalledWith(undefined));
    expect(await screen.findByLabelText("当前 renderer 工作区"))
      .toHaveTextContent("/native-project");
  });

  it("shows a native folder picker failure inside the shared chooser", async () => {
    mocks.platform.environment = "desktop";
    mocks.selectWorkdir.mockRejectedValueOnce(new Error("macOS folder picker failed"));
    render(<AgentWorkspace />);
    fireEvent.click(await screen.findByRole("button", { name: "打开工作区..." }));
    fireEvent.click(
      within(screen.getByRole("dialog", { name: "选择工作区" }))
        .getByRole("button", { name: "打开其他文件夹..." }),
    );

    expect(await screen.findByRole("alert"))
      .toHaveTextContent("macOS folder picker failed");
  });

  it("keeps the shared chooser underneath the Web directory browser", async () => {
    render(<AgentWorkspace />);
    fireEvent.click(await screen.findByRole("button", { name: "打开工作区..." }));
    fireEvent.click(
      within(screen.getByRole("dialog", { name: "选择工作区" }))
        .getByRole("button", { name: "打开其他文件夹..." }),
    );

    expect(screen.getByRole("dialog", { name: "选择工作目录" }))
      .toBeInTheDocument();
    expect(mocks.selectWorkdir).not.toHaveBeenCalled();
  });

  it("does not let an older Recent load overwrite a completed selection", async () => {
    const initialLoad = deferred<readonly RecentWorkspace[]>();
    mocks.listRecentWorkspaces.mockReturnValueOnce(initialLoad.promise);
    mocks.platform.environment = "desktop";
    mocks.selectWorkdir.mockResolvedValueOnce("/native-project");
    render(<AgentWorkspace />);

    fireEvent.click(screen.getByRole("button", { name: "打开工作区..." }));
    fireEvent.click(
      within(screen.getByRole("dialog", { name: "选择工作区" }))
        .getByRole("button", { name: "打开其他文件夹..." }),
    );
    expect(await screen.findByLabelText("当前 renderer 工作区"))
      .toHaveTextContent("/native-project");
    await act(async () => initialLoad.resolve([]));
    fireEvent.click(screen.getByRole("button", { name: "切换工作区入口" }));

    expect(
      within(screen.getByRole("dialog", { name: "选择工作区" }))
        .getByRole("button", { name: /native-project/ }),
    ).toBeInTheDocument();
  });
});
