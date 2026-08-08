/** 连接研讨应用 store、共享工作区选择器和纯展示组件。 */

import { useCallback, useEffect, useState } from "react";
import {
  WorkdirPicker,
  WorkspaceChooser,
  listRecentWorkspaces,
  rememberRecentWorkspace,
  type RecentWorkspace,
} from "../../agent";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { getPlatform } from "../../platform";
import { useDiscussionStore } from "../application";
import type { CreateDiscussionInput, HandoffAgentInput } from "../domain";
import { DiscussionHistoryRail } from "./DiscussionHistoryRail";
import { DiscussionView } from "./DiscussionView";
import { HandoffDialog } from "./HandoffDialog";
import { NewDiscussionDialog } from "./NewDiscussionDialog";
import "./discussion.css";

interface DiscussionWorkspaceProps {
  readonly active: boolean;
  readonly onAgentHandoff: (sessionId: string) => Promise<void>;
}

interface WorkdirRequest {
  readonly current: string;
  readonly apply: (path: string) => void;
}

/** 顶层容器负责外部 I/O，研讨展示树只接收快照和命令回调。 */
export function DiscussionWorkspace({
  active,
  onAgentHandoff,
}: DiscussionWorkspaceProps) {
  const discussions = useDiscussionStore((state) => state.discussions);
  const discussion = useDiscussionStore((state) => state.discussion);
  const connections = useDiscussionStore((state) => state.connections);
  const sessionConfigurations = useDiscussionStore(
    (state) => state.sessionConfigurations,
  );
  const loading = useDiscussionStore((state) => state.loading);
  const catalogLoading = useDiscussionStore((state) => state.catalogLoading);
  const commandPending = useDiscussionStore((state) => state.commandPending);
  const error = useDiscussionStore((state) => state.error);
  const attemptTimelines = useDiscussionStore((state) => state.attemptTimelines);
  const store = useDiscussionStore;
  const [newDialogOpen, setNewDialogOpen] = useState(false);
  const [handoffOpen, setHandoffOpen] = useState(false);
  const [recents, setRecents] = useState<readonly RecentWorkspace[]>([]);
  const [workdirRequest, setWorkdirRequest] = useState<WorkdirRequest | null>(null);
  const [browserOpen, setBrowserOpen] = useState(false);
  const [workdirError, setWorkdirError] = useState<string | null>(null);
  const [deleteConfirmationOpen, setDeleteConfirmationOpen] = useState(false);

  const loadWorkspaceData = useCallback(async (): Promise<void> => {
    await Promise.all([
      store.getState().load(),
      store.getState().loadCatalog(),
      listRecentWorkspaces().then(setRecents).catch(() => setRecents([])),
    ]);
  }, [store]);

  useEffect(() => {
    if (!active) return;
    // 外部快照只在一级入口激活时加载，Agent 交接后隐藏页面不会重复请求。
    void loadWorkspaceData();
  }, [active, loadWorkspaceData]);

  function chooseWorkdir(current: string, apply: (path: string) => void): void {
    setWorkdirError(null);
    setWorkdirRequest({ current, apply });
  }

  async function acceptWorkdir(path: string): Promise<void> {
    if (!workdirRequest) return;
    try {
      const selected = await rememberRecentWorkspace(path);
      workdirRequest.apply(selected.path);
      setRecents((current) => [
        selected,
        ...current.filter((item) => item.path !== selected.path),
      ].slice(0, 10));
      setWorkdirRequest(null);
      setBrowserOpen(false);
      setWorkdirError(null);
    } catch (reason) {
      setBrowserOpen(false);
      setWorkdirError(reason instanceof Error ? reason.message : "工作区无法打开");
    }
  }

  async function browseOther(): Promise<void> {
    if (!workdirRequest) return;
    const platform = getPlatform();
    if (platform.environment === "browser") {
      setBrowserOpen(true);
      return;
    }
    try {
      const path = await platform.selectWorkdir(workdirRequest.current || undefined);
      if (path) await acceptWorkdir(path);
    } catch (reason) {
      setWorkdirError(reason instanceof Error ? reason.message : "系统文件夹选择器无法打开");
    }
  }

  async function createAndStart(input: CreateDiscussionInput): Promise<void> {
    const created = await store.getState().createDiscussion(input);
    setNewDialogOpen(false);
    if (created.status === "draft") await store.getState().start();
  }

  async function createHandoff(
    agent: HandoffAgentInput,
    instruction: string,
  ): Promise<void> {
    const result = await store.getState().handoff(agent, instruction);
    setHandoffOpen(false);
    await onAgentHandoff(result.agent_session_id);
  }

  const initialWorkdir = discussion?.workdir ?? recents.find((item) => item.available)?.path ?? "";
  return (
    <div className="discussion-workspace" hidden={!active}>
      <div className="discussion-titlebar" aria-hidden="true">Trowel</div>
      <DiscussionHistoryRail
        discussions={discussions}
        currentId={discussion?.id ?? null}
        loading={loading}
        onOpen={(id) => void store.getState().open(id)}
        onNew={() => setNewDialogOpen(true)}
      />
      {discussion ? (
        <DiscussionView
          discussion={discussion}
          pendingCommand={commandPending}
          error={error}
          attemptTimelines={attemptTimelines}
          onStart={() => void store.getState().start()}
          onContinue={(progressionMode, additionalRounds) =>
            void store.getState().continueRound(progressionMode, additionalRounds)
          }
          onFinish={() => void store.getState().finish()}
          onStop={() => void store.getState().stop()}
          onResume={() => void store.getState().resume()}
          onDelete={() => setDeleteConfirmationOpen(true)}
          onAddMessage={(body, targetId) => store.getState().addMessage(body, targetId)}
          onMark={(roundNumber, participantId, marked) =>
            void store.getState().mark(roundNumber, participantId, marked)
          }
          onAnswerQuestion={(participantId, attemptId, requestId, answers) =>
            void store
              .getState()
              .answerQuestion(participantId, attemptId, requestId, answers)
          }
          onHandoff={() => setHandoffOpen(true)}
          onClearError={() => store.getState().clearError()}
        />
      ) : (
        <main className="discussion-blank">
          <div className="discussion-blank__inner">
            <header className="discussion-blank__brand">
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" />
                <circle cx="9" cy="7" r="4" />
                <path d="M22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75" />
              </svg>
              <div>
                <h1>Trowel 研讨</h1>
                <p>让多个模型独立判断，在同一轮共同公开结果</p>
              </div>
            </header>
            <section className="discussion-blank__start">
              <h2>开始</h2>
              <button type="button" onClick={() => setNewDialogOpen(true)}>
                <svg viewBox="0 0 24 24" aria-hidden="true">
                  <path d="M12 5v14M5 12h14" />
                </svg>
                <span>新建研讨</span>
              </button>
            </section>
          </div>
        </main>
      )}

      {newDialogOpen && (
        <NewDiscussionDialog
          connections={connections}
          sessionConfigurations={sessionConfigurations}
          loading={catalogLoading}
          creating={commandPending === "create"}
          initialWorkdir={initialWorkdir}
          onChooseWorkdir={chooseWorkdir}
          onCreate={(input) => void createAndStart(input).catch(() => undefined)}
          onCancel={() => setNewDialogOpen(false)}
        />
      )}
      {handoffOpen && discussion && (
        <HandoffDialog
          discussion={discussion}
          connections={connections}
          pending={commandPending === "handoff"}
          onChooseWorkdir={chooseWorkdir}
          onSubmit={(agent, instruction) =>
            void createHandoff(agent, instruction).catch(() => undefined)
          }
          onCancel={() => setHandoffOpen(false)}
        />
      )}
      {workdirRequest && (
        <WorkspaceChooser
          title="选择研讨工作区"
          recents={recents}
          error={workdirError}
          browseHint={getPlatform().environment === "desktop" ? "将继续使用 macOS 文件夹选择器" : "将继续使用 Trowel 分层目录浏览器"}
          onSelect={(path) => void acceptWorkdir(path)}
          onBrowseOther={() => void browseOther()}
          onCancel={() => {
            setWorkdirRequest(null);
            setBrowserOpen(false);
          }}
        />
      )}
      {workdirRequest && browserOpen && (
        <WorkdirPicker
          initialPath={workdirRequest.current || "~"}
          recents={recents.filter((item) => item.available).map((item) => item.path)}
          onSelect={(path) => void acceptWorkdir(path)}
          onCancel={() => setBrowserOpen(false)}
        />
      )}
      {deleteConfirmationOpen && discussion && (
        <ConfirmDialog
          title="删除这场研讨？"
          description="公开记录将不再出现在历史列表中，此操作无法撤销。"
          confirmLabel="删除研讨"
          tone="danger"
          onConfirm={() => {
            setDeleteConfirmationOpen(false);
            void store.getState().remove();
          }}
          onCancel={() => setDeleteConfirmationOpen(false)}
        />
      )}
    </div>
  );
}
