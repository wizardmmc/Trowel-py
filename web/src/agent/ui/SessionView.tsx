/** 组合通用会话外壳，并按 runtime 能力装配消息、输入区和侧栏。 */

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type MutableRefObject,
  type ReactNode,
} from "react";
import { useShallow } from "zustand/react/shallow";

import type { Turn } from "../domain";
import {
  CodexCommandDialogs,
  getRuntimePresentation,
  type CodexCommandDialogKind,
} from "../runtimes";
import type {
  AgentHistoryRow,
  CodexCommand,
  CodexReviewTarget,
} from "../application";
import {
  getAgentSessionDefaults,
  useCodexCommandRoster,
  useAgentStore,
  useAgentStoreFrameSelector,
  useSessionLifecycle,
} from "../application";
import type { NewSessionConfig } from "../../components/cc/NewSessionDialog";
import {
  loadNewSessionPreferences,
  saveNewSessionPreferences,
} from "../../components/cc/newSessionPreferences";
import { MessageList } from "./MessageList";
import { WorkspaceHome } from "./WorkspaceHome";
import { MultiSessionBar } from "../../components/cc/MultiSessionBar";
import { SessionBanners } from "../../components/cc/SessionBanners";
import { SessionComposer } from "../../components/cc/SessionComposer";
import { SessionHeader } from "../../components/cc/SessionHeader";
import { SessionOverlays } from "../../components/cc/SessionOverlays";
import { TodoBar } from "../../components/cc/TodoBar";
import { useElementHeight } from "../../components/cc/useElementHeight";
import { useSessionCatalogs } from "../../components/cc/useSessionCatalogs";
import { useStickyBottom } from "../../components/cc/useStickyBottom";
import "./agent.css";

export interface NewSessionWorkdirRequest {
  readonly id: number;
  readonly workdir: string;
}

interface SessionViewProps {
  readonly workdir: string;
  readonly onRequestChangeWorkdir?: () => void;
  readonly onRequestNewWorkdir?: () => void;
  readonly newSessionWorkdirRequest?: NewSessionWorkdirRequest | null;
  readonly onNewSessionWorkdirRequestHandled?: (id: number) => void;
  readonly onWorkdirActivated?: (workdir: string) => void;
  readonly emptyWorkspaceContent?: ReactNode;
}

const ACTIVE_PHASES = new Set([
  "awaiting_first",
  "thinking",
  "generating",
  "tool",
  "retrying",
  "compacting",
]);

const EMPTY_TURNS: readonly Turn[] = [];

interface SessionTranscriptPaneProps {
  readonly activeSid: string | null;
  readonly openedSubagentId: string | null;
  readonly composerH: number;
  readonly fallbackWorkdir: string;
  readonly emptyContent?: ReactNode;
  readonly onRequestChangeWorkdir?: () => void;
  readonly jumpToBottomRef: MutableRefObject<(() => void) | null>;
  readonly onCloseSubagent: () => void;
  readonly onRetryLast: () => void;
  readonly onAnswer: (answers: Record<string, string>) => void;
  readonly onCancel: () => void;
  readonly onApprovalDecision: (requestId: string, decision: string) => void;
  readonly onRevert: (turn: Turn) => void;
  readonly onOpenSubagent: (threadId: string) => void;
}

/** 高频消息树单独按帧订阅；Header、Composer 不跟随文字 delta 重渲染。 */
function SessionTranscriptPane({
  activeSid,
  openedSubagentId,
  composerH,
  fallbackWorkdir,
  emptyContent,
  onRequestChangeWorkdir,
  jumpToBottomRef,
  onCloseSubagent,
  onRetryLast,
  onAnswer,
  onCancel,
  onApprovalDecision,
  onRevert,
  onOpenSubagent,
}: SessionTranscriptPaneProps) {
  const selectActive = useCallback(
    (state: ReturnType<typeof useAgentStore.getState>) =>
      activeSid ? state.sessions[activeSid] ?? null : null,
    [activeSid],
  );
  const active = useAgentStoreFrameSelector(selectActive);
  const presentation = active
    ? getRuntimePresentation(active.runtime, active.capabilities)
    : null;
  const phase = active?.phase ?? "idle";
  const turns = active?.turns ?? EMPTY_TURNS;
  const openedSubagent = openedSubagentId
    ? active?.codexSubagents[openedSubagentId] ?? null
    : null;
  const viewPhase = openedSubagent?.state.phase ?? phase;
  const viewTurns = openedSubagent?.state.turns ?? turns;
  const streaming = ACTIVE_PHASES.has(phase);
  const viewStreaming = openedSubagent
    ? openedSubagent.status === "started" || openedSubagent.status === "progress"
    : streaming;
  const scrollRef = useRef<HTMLDivElement>(null);
  const { sticky, unread, stickyRef, pauseFollowing, jumpToBottom } =
    useStickyBottom(
      scrollRef,
      viewTurns.length,
      `${activeSid ?? "none"}:${openedSubagentId ?? "root"}`,
    );
  useEffect(() => {
    jumpToBottomRef.current = jumpToBottom;
    return () => {
      jumpToBottomRef.current = null;
    };
  }, [jumpToBottom, jumpToBottomRef]);

  return (
    <>
      <div
        ref={scrollRef}
        className={`cc-view__scroll${!active && emptyContent ? " cc-view__scroll--workspace" : ""}`}
        style={{ "--composer-h": `${composerH}px` } as CSSProperties}
      >
        {active ? (
          <>
            {openedSubagent && (
              <nav className="cc-child-breadcrumb" aria-label="Subagent 路径">
                <button type="button" onClick={onCloseSubagent}>
                  {active.displayTitle || active.name}
                </button>
                <span aria-hidden="true">/</span>
                <span>{openedSubagent.agentPath ?? openedSubagent.threadId}</span>
              </nav>
            )}
            {openedSubagent?.historyError && (
              <div className="cc-child-history-error" role="alert">
                {openedSubagent.historyError}
              </div>
            )}
            <MessageList
              key={`${activeSid}:${openedSubagentId ?? "root"}`}
              turns={viewTurns}
              streaming={viewStreaming}
              phase={viewPhase}
              scrollRef={scrollRef}
              sticky={sticky}
              followingRef={stickyRef}
              onLeaveBottom={pauseFollowing}
              onRetryLast={openedSubagent ? undefined : onRetryLast}
              onAnswer={onAnswer}
              onCancel={onCancel}
              onApprovalDecision={onApprovalDecision}
              onRevert={openedSubagent ? undefined : onRevert}
              workdir={active.workdir ?? fallbackWorkdir}
              presentation={presentation ?? undefined}
              sessionId={activeSid ?? undefined}
              codexSubagents={active.codexSubagents}
              onOpenSubagent={onOpenSubagent}
              emptyLabel={
                openedSubagent
                  ? openedSubagent.historyLoading
                    ? "正在读取 Subagent 记录…"
                    : "尚未收到 Subagent 输出。"
                  : undefined
              }
            />
          </>
        ) : emptyContent ? (
          emptyContent
        ) : fallbackWorkdir ? (
          <div className="cc-empty cc-empty--noactive">
            <div>未选择 session</div>
            <div className="cc-empty__hint">
              点左侧多开栏选一个，或 + 新开一个。
            </div>
          </div>
        ) : (
          <div className="cc-empty cc-empty--noactive">
            <div>未选择工作目录</div>
            {onRequestChangeWorkdir && (
              <button
                type="button"
                className="cc-empty__action"
                onClick={onRequestChangeWorkdir}
              >
                <svg viewBox="0 0 24 24" aria-hidden="true">
                  <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
                </svg>
                选择工作目录
              </button>
            )}
          </div>
        )}
      </div>
      {active && !sticky && (
        <button
          type="button"
          className="cc-jump-latest"
          onClick={jumpToBottom}
          aria-label="回到最新"
        >
          {unread > 0 && (
            <span className="cc-jump-latest__dot" aria-hidden="true" />
          )}
          <svg
            className="cc-jump-latest__arrow"
            viewBox="0 0 24 24"
            aria-hidden="true"
          >
            <path d="M12 5v14M6 13l6 6 6-6" />
          </svg>
          <span>{unread > 0 ? `${unread} 新` : "最新"}</span>
        </button>
      )}
    </>
  );
}

export function SessionView({
  workdir,
  onRequestChangeWorkdir,
  onRequestNewWorkdir,
  newSessionWorkdirRequest = null,
  onNewSessionWorkdirRequestHandled,
  onWorkdirActivated,
  emptyWorkspaceContent,
}: SessionViewProps) {
  const activeSid = useAgentStore((s) => s.activeSid);
  const active = useAgentStore(
    useShallow((state) => {
      const session = activeSid ? state.sessions[activeSid] ?? null : null;
      // turns 是唯一逐 delta 增长的大对象，由 SessionTranscriptPane 单独订阅。
      return session ? { ...session, turns: EMPTY_TURNS } : null;
    }),
  );
  const activeTurnCount = useAgentStore((state) =>
    activeSid ? state.sessions[activeSid]?.turns.length ?? 0 : 0,
  );
  const startSession = useAgentStore((s) => s.startSession);
  const loadHistoryIntoView = useAgentStore((s) => s.loadHistoryIntoView);
  const loadCodexSubagentHistory = useAgentStore(
    (s) => s.loadCodexSubagentHistory,
  );
  const send = useAgentStore((s) => s.send);
  const interrupt = useAgentStore((s) => s.interrupt);
  const answerElicit = useAgentStore((s) => s.answerElicit);
  const cancelElicit = useAgentStore((s) => s.cancelElicit);
  const answerApproval = useAgentStore((s) => s.answerApproval);
  const revertTurn = useAgentStore((s) => s.revertTurn);
  const history = useAgentStore((s) => s.history);
  const loadingHistory = useAgentStore((s) => s.loadingHistory);
  const loadingMoreHistory = useAgentStore((s) => s.loadingMoreHistory);
  const historyHasMore = useAgentStore((s) => s.historyHasMore);
  const historyError = useAgentStore((s) => s.historyError);
  const refreshHistory = useAgentStore((s) => s.refreshHistory);
  const loadMoreHistory = useAgentStore((s) => s.loadMoreHistory);
  const updateSessionSettings = useAgentStore((s) => s.updateSessionSettings);
  const selectSessionPermissionPreset = useAgentStore(
    (s) => s.selectSessionPermissionPreset,
  );
  const compactCodex = useAgentStore((s) => s.compactCodex);
  const startCodexReview = useAgentStore((s) => s.startCodexReview);

  const [revertTarget, setRevertTarget] = useState<Turn | null>(null);
  const [showModelPicker, setShowModelPicker] = useState(false);
  const [showEffortPicker, setShowEffortPicker] = useState(false);
  const [showNewDialog, setShowNewDialog] = useState(false);
  const [newSessionWorkdir, setNewSessionWorkdir] = useState<string | null>(
    null,
  );
  const [workRailOpen, setWorkRailOpen] = useState(false);
  const [openedSubagentId, setOpenedSubagentId] = useState<string | null>(null);
  const [commandDialog, setCommandDialog] =
    useState<CodexCommandDialogKind>(null);
  const [reviewPending, setReviewPending] = useState(false);
  const [reviewError, setReviewError] = useState<string | null>(null);
  const reviewRequestRef = useRef<symbol | null>(null);
  const handledNewSessionRequestRef = useRef<number | null>(null);
  const [commandNotice, setCommandNotice] = useState<{
    readonly sessionId: string;
    readonly level: "loading" | "success" | "error";
    readonly text: string;
  } | null>(null);
  const [newSessionInitialConfig, setNewSessionInitialConfig] =
    useState<NewSessionConfig | null>(null);
  const catalogWorkdir =
    active?.workdir ??
    newSessionWorkdir ??
    newSessionWorkdirRequest?.workdir ??
    workdir;
  const {
    slashItems,
    models,
    codexModels,
    codexCatalogError,
    runtimesState,
    loadRuntimes,
    loadCodexModels,
  } = useSessionCatalogs(catalogWorkdir);
  const activePresentation = active
    ? getRuntimePresentation(active.runtime, active.capabilities)
    : null;
  const commandRoster = useCodexCommandRoster(
    activeSid,
    activePresentation?.composerActions.slashSource === "codex",
  );
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const [composerRef, composerH] = useElementHeight<HTMLDivElement>();
  const jumpToBottomRef = useRef<(() => void) | null>(null);

  const phase = active?.phase ?? "idle";
  const openedSubagent = openedSubagentId
    ? active?.codexSubagents[openedSubagentId] ?? null
    : null;
  const meta = active?.meta ?? null;
  const effort = active?.effort ?? null;
  const streaming = ACTIVE_PHASES.has(phase);
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setRevertTarget(null);
    setWorkRailOpen(false);
    setOpenedSubagentId(null);
    setCommandDialog(null);
    setReviewPending(false);
    setReviewError(null);
    reviewRequestRef.current = null;
  }, [activeSid]);

  function openSubagent(threadId: string) {
    setOpenedSubagentId(threadId);
    void loadCodexSubagentHistory(threadId);
  }

  function locateSubagent(threadId: string) {
    setCommandDialog(null);
    setOpenedSubagentId(null);
    requestAnimationFrame(() => {
      const target = document.querySelector<HTMLElement>(
        `[data-subagent-thread-id="${CSS.escape(threadId)}"]`,
      );
      if (!target) return;
      target.scrollIntoView({ behavior: "smooth", block: "center" });
      target.dataset.highlight = "true";
      window.setTimeout(() => delete target.dataset.highlight, 1800);
    });
  }

  useEffect(() => {
    if (commandNotice?.level !== "success") return;
    const timer = window.setTimeout(() => setCommandNotice(null), 5000);
    return () => window.clearTimeout(timer);
  }, [commandNotice]);

  useSessionLifecycle({
    workdir,
    activeWorkdir: active?.workdir ?? null,
    activeConnected: active?.connected ?? false,
    activeTurnCount,
    activeHasAbort: Boolean(active?.abort),
    activeSid,
    refreshHistory,
    loadHistoryIntoView,
  });

  /** 读取最近配置并打开指定目录的新会话对话框。 */
  const prepareNewSession = useCallback(async (targetWorkdir: string) => {
    setCreateError(null);
    const latest = await getAgentSessionDefaults().catch(() => null);
    setNewSessionInitialConfig(latest ?? loadNewSessionPreferences());
    setNewSessionWorkdir(targetWorkdir);
    setShowNewDialog(true);
  }, []);

  useEffect(() => {
    const request = newSessionWorkdirRequest;
    if (!request || handledNewSessionRequestRef.current === request.id) return;
    handledNewSessionRequestRef.current = request.id;
    onNewSessionWorkdirRequestHandled?.(request.id);
    void prepareNewSession(request.workdir);
  }, [
    newSessionWorkdirRequest,
    onNewSessionWorkdirRequestHandled,
    prepareNewSession,
  ]);

  async function handlePick(row: AgentHistoryRow) {
    if (!row.native_session_id) return;
    const targetWorkdir = active?.workdir ?? workdir;
    if (!targetWorkdir.trim()) return;
    try {
      await startSession({
        workdir: targetWorkdir,
        runtime: row.runtime,
        resume_from: row.native_session_id,
        resume_title: row.title,
      });
      await loadHistoryIntoView();
    } catch {
      return;
    }
  }

  async function handleNewSameWorkdir() {
    const targetWorkdir = active?.workdir ?? workdir;
    if (!targetWorkdir.trim()) {
      (onRequestNewWorkdir ?? onRequestChangeWorkdir)?.();
      return;
    }
    await prepareNewSession(targetWorkdir);
  }

  async function handleCreate(config: NewSessionConfig) {
    const targetWorkdir = newSessionWorkdir ?? active?.workdir ?? workdir;
    if (!targetWorkdir.trim()) {
      setCreateError("需要先选择工作目录");
      return;
    }
    setCreating(true);
    setCreateError(null);
    try {
      await startSession({ workdir: targetWorkdir, ...config });
      saveNewSessionPreferences(config);
      setNewSessionInitialConfig(config);
      setNewSessionWorkdir(null);
      setShowNewDialog(false);
      onWorkdirActivated?.(targetWorkdir);
    } catch (err) {
      setCreateError((err as Error).message);
    } finally {
      setCreating(false);
    }
  }

  function handleRetryLast() {
    const turns = activeSid
      ? useAgentStore.getState().sessions[activeSid]?.turns ?? EMPTY_TURNS
      : EMPTY_TURNS;
    const last = turns[turns.length - 1];
    if (last) {
      void send(last.userText);
    }
  }

  const lostTurns = (() => {
    if (!revertTarget) return [];
    const turns = activeSid
      ? useAgentStore.getState().sessions[activeSid]?.turns ?? EMPTY_TURNS
      : EMPTY_TURNS;
    const idx = turns.findIndex((t) => t.id === revertTarget.id);
    return idx === -1 ? [] : turns.slice(idx);
  })();
  const showGoalPanel = activePresentation?.sidePanelSections.goal ?? false;
  const showPlanPanel = activePresentation?.sidePanelSections.plan ?? false;
  const workRailLabel =
    showGoalPanel && showPlanPanel
      ? "目标与计划"
      : showGoalPanel
        ? "目标"
        : "计划";
  const workSummary = showPlanPanel
    ? `计划 ${active?.plan?.steps.filter((step) => step.status === "completed").length ?? 0}/${active?.plan?.steps.length ?? 0}`
    : showGoalPanel
      ? "目标"
      : null;
  const emptyContent = workdir ? (
    <WorkspaceHome
      workdir={workdir}
      history={history}
      loadingHistory={loadingHistory}
      loadingMoreHistory={loadingMoreHistory}
      historyHasMore={historyHasMore}
      historyError={historyError}
      onNewSession={() => void handleNewSameWorkdir()}
      onSwitchWorkspace={() => onRequestChangeWorkdir?.()}
      onPickHistory={(row) => void handlePick(row)}
      onLoadMoreHistory={() => void loadMoreHistory()}
      onRetryHistory={() => void refreshHistory(workdir)}
    />
  ) : (
    emptyWorkspaceContent
  );

  async function handleRevertConfirm() {
    if (!revertTarget?.turnId) return;
    if (lostTurns.length === 0) {
      setRevertTarget(null);
      return;
    }
    const turnId = revertTarget.turnId;
    setRevertTarget(null);
    await revertTurn(turnId);
  }

  function handleCodexCommand(command: CodexCommand, rawText: string) {
    if (!activeSid) return;
    if (rawText.trim() !== `/${command.name}`) {
      setCommandNotice({
        sessionId: activeSid,
        level: "error",
        text: `/${command.name} 不接受文本参数，请从面板中选择目标`,
      });
      return;
    }
    setReviewError(null);
    if (command.action === "status") setCommandDialog("status");
    if (command.action === "diff") setCommandDialog("diff");
    if (command.action === "goal") setWorkRailOpen(true);
    if (command.action === "review") setCommandDialog("review");
    if (command.action === "agent") setCommandDialog("agent");
    if (command.action === "compact") void handleCompact(activeSid);
  }

  async function handleCompact(sessionId: string) {
    setCommandNotice({
      sessionId,
      level: "loading",
      text: "正在启动上下文压缩…",
    });
    try {
      await compactCodex();
      setCommandNotice({
        sessionId,
        level: "success",
        text: "上下文压缩已启动",
      });
    } catch (error) {
      setCommandNotice({
        sessionId,
        level: "error",
        text: error instanceof Error ? error.message : String(error),
      });
    }
  }

  async function handleStartReview(target: CodexReviewTarget) {
    const sessionId = activeSid;
    if (!sessionId) return;
    const requestId = Symbol("codex-review");
    reviewRequestRef.current = requestId;
    setReviewPending(true);
    setReviewError(null);
    try {
      await startCodexReview(target);
      if (
        reviewRequestRef.current === requestId &&
        useAgentStore.getState().activeSid === sessionId
      ) {
        setCommandDialog(null);
      }
    } catch (error) {
      if (
        reviewRequestRef.current === requestId &&
        useAgentStore.getState().activeSid === sessionId
      ) {
        setReviewError(error instanceof Error ? error.message : String(error));
      }
    } finally {
      if (
        reviewRequestRef.current === requestId &&
        useAgentStore.getState().activeSid === sessionId
      ) {
        reviewRequestRef.current = null;
        setReviewPending(false);
      }
    }
  }

  return (
    <div className="cc-3col">
      <MultiSessionBar
        onNewSameWorkdir={() => void handleNewSameWorkdir()}
        onChangeWorkdir={() =>
          (onRequestNewWorkdir ?? onRequestChangeWorkdir)?.()
        }
        onActivateWorkdir={onWorkdirActivated}
      />
      <div className="cc-view">
        <SessionHeader
          phase={phase}
          meta={meta}
          runtimeLabel={activePresentation?.shortLabel ?? "Agent"}
          streaming={streaming}
          models={models}
          history={history}
          loadingHistory={loadingHistory}
          loadingMoreHistory={loadingMoreHistory}
          historyHasMore={historyHasMore}
          historyError={historyError}
          workdir={active?.workdir ?? workdir}
          nativeSessionId={active?.nativeSessionId ?? null}
          workSummary={workSummary}
          workRailLabel={`打开${workRailLabel}`}
          onToggleWorkRail={() => setWorkRailOpen(true)}
          onInterrupt={
            activePresentation?.composerActions.interrupt
              ? () => void interrupt()
              : undefined
          }
          onPickHistory={(row) => void handlePick(row)}
          onLoadMoreHistory={() => void loadMoreHistory()}
          onRetryHistory={() => void refreshHistory(active?.workdir ?? workdir)}
          onNew={() => void handleNewSameWorkdir()}
          onRequestChangeWorkdir={onRequestChangeWorkdir}
        />
        <SessionBanners active={active} activeSid={activeSid} />
        <SessionTranscriptPane
          activeSid={activeSid}
          openedSubagentId={openedSubagentId}
          composerH={composerH}
          fallbackWorkdir={workdir}
          emptyContent={emptyContent}
          onRequestChangeWorkdir={onRequestChangeWorkdir}
          jumpToBottomRef={jumpToBottomRef}
          onCloseSubagent={() => setOpenedSubagentId(null)}
          onRetryLast={handleRetryLast}
          onAnswer={(answers) => void answerElicit(answers)}
          onCancel={() => void cancelElicit()}
          onApprovalDecision={(requestId, decision) =>
            void answerApproval(requestId, decision)
          }
          onRevert={(turn) => setRevertTarget(turn)}
          onOpenSubagent={openSubagent}
        />
        <div ref={composerRef}>
          {!openedSubagent && active && (
            <>
          {commandNotice && commandNotice.sessionId === activeSid && (
            <div
              className={`cc-command-notice cc-command-notice--${commandNotice.level}`}
              role={commandNotice.level === "error" ? "alert" : "status"}
            >
              <span>{commandNotice.text}</span>
              {commandNotice.level !== "loading" && (
                <button
                  type="button"
                  onClick={() => setCommandNotice(null)}
                  aria-label="关闭命令提示"
                >
                  ×
                </button>
              )}
            </div>
          )}
          {active?.settingsNotice && (
            <div className="cc-settings-notice" role="status">
              {active.settingsNotice}
            </div>
          )}
          <SessionComposer
            active={active}
            activeSid={activeSid}
            streaming={streaming}
            slashItems={slashItems}
            ccModels={models}
            codexModels={codexModels}
            codexCatalogError={codexCatalogError}
            codexCommands={commandRoster.commands}
            codexCommandsLoading={commandRoster.loading}
            codexCommandsError={commandRoster.error}
            onRetryCodexCommands={commandRoster.retry}
            onCodexCommand={handleCodexCommand}
            onRetryCodexCatalog={loadCodexModels}
            onSend={(text) => {
              void send(text);
              requestAnimationFrame(() => jumpToBottomRef.current?.());
            }}
            onInterrupt={() => void interrupt()}
            onUpdateSettings={(model, nextEffort) =>
              void updateSessionSettings(model, nextEffort)
            }
            onSelectPermissionPreset={(preset) =>
              void selectSessionPermissionPreset(preset)
            }
            onRequestModelPicker={() => setShowModelPicker(true)}
            onRequestEffortPicker={() => setShowEffortPicker(true)}
          />
            </>
          )}
        </div>
        <SessionOverlays
          revert={
            revertTarget
              ? {
                  lostTurns,
                  onConfirm: () => void handleRevertConfirm(),
                  onCancel: () => setRevertTarget(null),
                }
              : null
          }
          newSession={
            showNewDialog
              ? {
                  workdir: newSessionWorkdir ?? active?.workdir ?? workdir,
                  initialConfig: newSessionInitialConfig,
                  runtimesState,
                  onRetryRuntimes: loadRuntimes,
                  creating,
                  error: createError,
                  ccModels: models,
                  codexModels,
                  codexCatalogError,
                  onRetryCodexCatalog: loadCodexModels,
                  onCreate: (config) => void handleCreate(config),
                  onCancel: () => {
                    setShowNewDialog(false);
                    setNewSessionWorkdir(null);
                    setCreateError(null);
                  },
                }
              : null
          }
          modelPicker={
            showModelPicker
              ? {
                  models,
                  currentModel: meta
                    ? models.find(
                        (model) =>
                          model.real_model === meta.model ||
                          model.value === meta.model,
                      )?.value ??
                      meta.model ??
                      ""
                    : "",
                  onSelect: (value) => {
                    void send(`/model ${value}`);
                    setShowModelPicker(false);
                  },
                  onCancel: () => setShowModelPicker(false),
                }
              : null
          }
          effortPicker={
            showEffortPicker
              ? {
                  currentEffort: effort,
                  onSelect: (value) => {
                    void send(`/effort ${value}`);
                    setShowEffortPicker(false);
                  },
                  onCancel: () => setShowEffortPicker(false),
                }
              : null
          }
        />
        {activePresentation?.composerActions.slashSource === "codex" && (
          <CodexCommandDialogs
            kind={commandDialog}
            active={active}
            onClose={() => {
              if (!reviewPending) setCommandDialog(null);
            }}
            onStartReview={(target) => void handleStartReview(target)}
            reviewPending={reviewPending}
            reviewError={reviewError}
            onLocateSubagent={locateSubagent}
          />
        )}
      </div>
      <TodoBar
        drawerOpen={workRailOpen}
        onCloseDrawer={() => setWorkRailOpen(false)}
      />
      {workRailOpen && (
        <button
          type="button"
          className="cc-workrail-backdrop"
          aria-label={`关闭${workRailLabel}`}
          onClick={() => setWorkRailOpen(false)}
        />
      )}
    </div>
  );
}
