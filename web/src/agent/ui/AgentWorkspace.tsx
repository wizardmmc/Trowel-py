/** 组合 renderer 本地工作区状态、统一选择器与 Agent 会话外壳。 */

import { useCallback, useEffect, useRef, useState } from "react";

import {
  listRecentWorkspaces,
  rememberRecentWorkspace,
  type RecentWorkspace,
} from "../application/workdirs";
import { useAgentStore } from "../application/store";
import { getPlatform } from "../../platform";
import { SessionView, type NewSessionWorkdirRequest } from "./SessionView";
import { WorkdirPicker } from "./WorkdirPicker";
import { WorkspaceChooser } from "./WorkspaceChooser";
import { WorkspaceStart } from "./WorkspaceStart";

type WorkspaceChooserIntent = "workspace" | "new";

/** 把刚打开的工作区移动到本地 Recent 快照首位。 */
function moveRecentToFront(
  recents: readonly RecentWorkspace[],
  selected: RecentWorkspace,
): readonly RecentWorkspace[] {
  return [selected, ...recents.filter((item) => item.path !== selected.path)].slice(
    0,
    10,
  );
}

/** 持有当前 renderer 的工作区选择，不把它写入共享 SessionHub。 */
export function AgentWorkspace() {
  const showWorkspaceHome = useAgentStore((state) => state.showWorkspaceHome);
  const [currentWorkdir, setCurrentWorkdir] = useState("");
  const [recents, setRecents] = useState<readonly RecentWorkspace[]>([]);
  const [recentsLoading, setRecentsLoading] = useState(true);
  const [recentsError, setRecentsError] = useState<string | null>(null);
  const [chooserIntent, setChooserIntent] =
    useState<WorkspaceChooserIntent | null>(null);
  const [chooserError, setChooserError] = useState<string | null>(null);
  const [webBrowserOpen, setWebBrowserOpen] = useState(false);
  const [newSessionRequest, setNewSessionRequest] =
    useState<NewSessionWorkdirRequest | null>(null);
  const requestSequence = useRef(0);
  const recentLoadGeneration = useRef(0);
  const selectionGeneration = useRef(0);

  /** 从共享 Agent Service 读取 Recent，并只接受当前代请求的结果。 */
  const loadRecents = useCallback(async (): Promise<void> => {
    const generation = ++recentLoadGeneration.current;
    try {
      const loaded = await listRecentWorkspaces();
      if (generation === recentLoadGeneration.current) setRecents(loaded);
    } catch (error) {
      if (generation === recentLoadGeneration.current) {
        setRecentsError(
          error instanceof Error ? error.message : "最近工作区读取失败",
        );
      }
    } finally {
      if (generation === recentLoadGeneration.current) {
        setRecentsLoading(false);
      }
    }
  }, []);

  /** 在用户主动重试时先恢复加载状态，再发起新一代请求。 */
  const refreshRecents = useCallback((): Promise<void> => {
    setRecentsLoading(true);
    setRecentsError(null);
    return loadRecents();
  }, [loadRecents]);

  useEffect(() => {
    // Recent 是外部服务状态；loadRecents 只在请求 settle 后更新 React 状态。
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void loadRecents();
  }, [loadRecents]);

  /** 打开统一工作区选择器，并记录选择后的下一步。 */
  function openChooser(intent: WorkspaceChooserIntent): void {
    setChooserError(null);
    setChooserIntent(intent);
  }

  /** 保存路径并按普通切换或新建会话意图继续。 */
  async function finishSelection(
    path: string,
    intent: WorkspaceChooserIntent,
  ): Promise<void> {
    const generation = ++selectionGeneration.current;
    recentLoadGeneration.current += 1;
    setChooserError(null);
    try {
      const selected = await rememberRecentWorkspace(path);
      if (generation !== selectionGeneration.current) return;
      setRecents((current) => moveRecentToFront(current, selected));
      setRecentsLoading(false);
      setChooserIntent(null);
      setWebBrowserOpen(false);
      if (intent === "workspace") {
        showWorkspaceHome();
        setCurrentWorkdir(selected.path);
        return;
      }
      requestSequence.current += 1;
      setNewSessionRequest({
        id: requestSequence.current,
        workdir: selected.path,
      });
    } catch (error) {
      if (generation !== selectionGeneration.current) return;
      setWebBrowserOpen(false);
      setChooserError(
        error instanceof Error ? error.message : "工作区无法打开",
      );
    }
  }

  /** 根据当前 renderer 环境打开 macOS 或 Web 目录选择器。 */
  async function browseOtherFolder(): Promise<void> {
    const intent = chooserIntent;
    if (!intent) return;
    const platform = getPlatform();
    if (platform.environment === "browser") {
      setWebBrowserOpen(true);
      return;
    }
    try {
      const selected = await platform.selectWorkdir(currentWorkdir || undefined);
      if (selected) await finishSelection(selected, intent);
    } catch (error) {
      setChooserError(
        error instanceof Error ? error.message : "系统文件夹选择器无法打开",
      );
    }
  }

  const platform = getPlatform();
  return (
    <>
      <SessionView
        workdir={currentWorkdir}
        onRequestChangeWorkdir={() => openChooser("workspace")}
        onRequestNewWorkdir={() => openChooser("new")}
        newSessionWorkdirRequest={newSessionRequest}
        onNewSessionWorkdirRequestHandled={(id) => {
          setNewSessionRequest((request) =>
            request?.id === id ? null : request,
          );
        }}
        onWorkdirActivated={setCurrentWorkdir}
        emptyWorkspaceContent={
          <WorkspaceStart
            recents={recents}
            loading={recentsLoading}
            error={recentsError}
            onOpenWorkspace={() => openChooser("workspace")}
            onNewSession={() => openChooser("new")}
            onOpenHistory={() => openChooser("workspace")}
            onSelectRecent={(path) => void finishSelection(path, "workspace")}
            onRetry={() => void refreshRecents()}
          />
        }
      />

      {chooserIntent && (
        <WorkspaceChooser
          title={
            chooserIntent === "new"
              ? "选择新会话的工作区"
              : "选择工作区"
          }
          recents={recents}
          error={chooserError}
          browseHint={
            platform.environment === "desktop"
              ? "将继续使用 macOS 文件夹选择器"
              : "将继续使用 Trowel 分层目录浏览器"
          }
          onSelect={(path) => void finishSelection(path, chooserIntent)}
          onBrowseOther={() => void browseOtherFolder()}
          onCancel={() => {
            setChooserIntent(null);
            setChooserError(null);
          }}
        />
      )}

      {chooserIntent && webBrowserOpen && (
        <WorkdirPicker
          initialPath={currentWorkdir || "~"}
          recents={recents.filter((item) => item.available).map((item) => item.path)}
          onSelect={(path) => void finishSelection(path, chooserIntent)}
          onCancel={() => setWebBrowserOpen(false)}
        />
      )}
    </>
  );
}
