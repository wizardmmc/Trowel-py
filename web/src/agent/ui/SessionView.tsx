import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type MutableRefObject,
} from "react";
import { useShallow } from "zustand/react/shallow";

import type { Turn } from "../domain";
import type {
  AgentHistoryRow,
  CodexCommand,
  CodexReviewTarget,
} from "../application";
import {
  getAgentSessionDefaults,
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
import { MultiSessionBar } from "../../components/cc/MultiSessionBar";
import { SessionBanners } from "../../components/cc/SessionBanners";
import { SessionComposer } from "../../components/cc/SessionComposer";
import { SessionHeader } from "../../components/cc/SessionHeader";
import { SessionOverlays } from "../../components/cc/SessionOverlays";
import {
  CodexCommandDialogs,
  type CodexCommandDialogKind,
} from "../../components/cc/CodexCommandDialogs";
import { TodoBar } from "../../components/cc/TodoBar";
import { useElementHeight } from "../../components/cc/useElementHeight";
import { useSessionCatalogs } from "../../components/cc/useSessionCatalogs";
import { useStickyBottom } from "../../components/cc/useStickyBottom";
import { useCodexCommandRoster } from "../../components/cc/useCodexCommandRoster";
import "./agent.css";

interface SessionViewProps {
  readonly workdir: string;
  readonly onRequestChangeWorkdir?: () => void;
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
  const { sticky, unread, pauseFollowing, jumpToBottom } = useStickyBottom(
    scrollRef,
    viewTurns.length,
    `${activeSid ?? "none"}:${openedSubagentId ?? "root"}`,
  );
  jumpToBottomRef.current = jumpToBottom;
  useEffect(
    () => () => {
      jumpToBottomRef.current = null;
    },
    [jumpToBottomRef],
  );

  return (
    <>
      <div
        ref={scrollRef}
        className="cc-view__scroll"
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
              onLeaveBottom={pauseFollowing}
              onRetryLast={openedSubagent ? undefined : onRetryLast}
              onAnswer={onAnswer}
              onCancel={onCancel}
              onApprovalDecision={onApprovalDecision}
              onRevert={openedSubagent ? undefined : onRevert}
              workdir={active.workdir ?? fallbackWorkdir}
              runtime={active.runtime}
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
        ) : (
          <div className="cc-empty cc-empty--noactive">
            <div>未选择 session</div>
            <div className="cc-empty__hint">
              点左侧多开栏选一个，或 + 新开一个。
            </div>
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
  const [workRailOpen, setWorkRailOpen] = useState(false);
  const [openedSubagentId, setOpenedSubagentId] = useState<string | null>(null);
  const [commandDialog, setCommandDialog] =
    useState<CodexCommandDialogKind>(null);
  const [reviewPending, setReviewPending] = useState(false);
  const [reviewError, setReviewError] = useState<string | null>(null);
  const reviewRequestRef = useRef<symbol | null>(null);
  const [commandNotice, setCommandNotice] = useState<{
    readonly sessionId: string;
    readonly level: "loading" | "success" | "error";
    readonly text: string;
  } | null>(null);
  const [newSessionInitialConfig, setNewSessionInitialConfig] =
    useState<NewSessionConfig | null>(null);
  const {
    slashItems,
    models,
    codexModels,
    codexCatalogError,
    runtimesState,
    loadRuntimes,
    loadCodexModels,
  } = useSessionCatalogs(workdir);
  const commandRoster = useCodexCommandRoster(
    activeSid,
    active?.runtime ?? null,
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

  async function handlePick(row: AgentHistoryRow) {
    if (!row.native_session_id) return;
    try {
      await startSession({
        workdir,
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
    setCreateError(null);
    const latest = await getAgentSessionDefaults().catch(() => null);
    setNewSessionInitialConfig(latest ?? loadNewSessionPreferences());
    setShowNewDialog(true);
  }

  async function handleCreate(config: NewSessionConfig) {
    setCreating(true);
    setCreateError(null);
    try {
      await startSession({ workdir, ...config });
      saveNewSessionPreferences(config);
      setNewSessionInitialConfig(config);
      setShowNewDialog(false);
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
  const workSummary =
    active?.runtime === "codex"
      ? `目标 ${active.plan?.steps.filter((step) => step.status === "completed").length ?? 0}/${active.plan?.steps.length ?? 0}`
      : null;

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
        onChangeWorkdir={() => onRequestChangeWorkdir?.()}
      />
      <div className="cc-view">
        <SessionHeader
          phase={phase}
          meta={meta}
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
          workRailLabel="打开目标与计划"
          onToggleWorkRail={() => setWorkRailOpen(true)}
          onInterrupt={() => void interrupt()}
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
          {!openedSubagent && (
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
                  workdir,
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
      </div>
      <TodoBar
        drawerOpen={workRailOpen}
        onCloseDrawer={() => setWorkRailOpen(false)}
      />
      {workRailOpen && (
        <button
          type="button"
          className="cc-workrail-backdrop"
          aria-label="关闭目标与计划"
          onClick={() => setWorkRailOpen(false)}
        />
      )}
    </div>
  );
}
