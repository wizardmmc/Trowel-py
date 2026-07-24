import { useEffect, useRef, useState, type CSSProperties } from "react";

import type { Turn } from "../../stores/ccStore";
import {
  useCcStore,
  useActiveSession,
} from "../../stores/ccStore";
import type { AgentHistoryRow } from "../../api/agent";
import type { NewSessionConfig } from "./NewSessionDialog";
import {
  loadNewSessionPreferences,
  saveNewSessionPreferences,
} from "./newSessionPreferences";
import { MessageList } from "./MessageList";
import { MultiSessionBar } from "./MultiSessionBar";
import { SessionBanners } from "./SessionBanners";
import { SessionComposer } from "./SessionComposer";
import { SessionHeader } from "./SessionHeader";
import { SessionOverlays } from "./SessionOverlays";
import { TodoBar } from "./TodoBar";
import { useElementHeight } from "./useElementHeight";
import { useSessionCatalogs } from "./useSessionCatalogs";
import { useSessionLifecycle } from "./useSessionLifecycle";
import { useStickyBottom } from "./useStickyBottom";
import "./cc.css";

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

export function SessionView({
  workdir,
  onRequestChangeWorkdir,
}: SessionViewProps) {
  const active = useActiveSession();
  const startSession = useCcStore((s) => s.startSession);
  const loadHistoryIntoView = useCcStore((s) => s.loadHistoryIntoView);
  const send = useCcStore((s) => s.send);
  const interrupt = useCcStore((s) => s.interrupt);
  const answerElicit = useCcStore((s) => s.answerElicit);
  const cancelElicit = useCcStore((s) => s.cancelElicit);
  const answerApproval = useCcStore((s) => s.answerApproval);
  const revertTurn = useCcStore((s) => s.revertTurn);
  const activeSid = useCcStore((s) => s.activeSid);
  const history = useCcStore((s) => s.history);
  const loadingHistory = useCcStore((s) => s.loadingHistory);
  const loadingMoreHistory = useCcStore((s) => s.loadingMoreHistory);
  const historyHasMore = useCcStore((s) => s.historyHasMore);
  const historyError = useCcStore((s) => s.historyError);
  const refreshHistory = useCcStore((s) => s.refreshHistory);
  const loadMoreHistory = useCcStore((s) => s.loadMoreHistory);
  const updateSessionSettings = useCcStore((s) => s.updateSessionSettings);

  const [revertTarget, setRevertTarget] = useState<Turn | null>(null);
  const [showModelPicker, setShowModelPicker] = useState(false);
  const [showEffortPicker, setShowEffortPicker] = useState(false);
  const [showNewDialog, setShowNewDialog] = useState(false);
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
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const [composerRef, composerH] = useElementHeight<HTMLDivElement>();

  const phase = active?.phase ?? "idle";
  const turns = active?.turns ?? [];
  // stickyRef 让滚动 effect 读取最新跟随状态而不重复订阅。
  const scrollRef = useRef<HTMLDivElement>(null);
  const { sticky, unread, stickyRef, jumpToBottom } = useStickyBottom(
    scrollRef,
    turns.length,
  );
  const meta = active?.meta ?? null;
  const effort = active?.effort ?? null;
  const streaming = ACTIVE_PHASES.has(phase);
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setRevertTarget(null);
  }, [activeSid]);

  useSessionLifecycle({
    workdir,
    active,
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
      });
      await loadHistoryIntoView();
    } catch {
    }
  }

  function handleNewSameWorkdir() {
    setCreateError(null);
    setNewSessionInitialConfig(loadNewSessionPreferences());
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
    const last = turns[turns.length - 1];
    if (last) {
      void send(last.userText);
    }
  }

  const lostTurns = (() => {
    if (!revertTarget) return [];
    const idx = turns.findIndex((t) => t.id === revertTarget.id);
    return idx === -1 ? [] : turns.slice(idx);
  })();

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

  return (
    <div className="cc-3col">
      <MultiSessionBar
        onNewSameWorkdir={handleNewSameWorkdir}
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
          onInterrupt={() => void interrupt()}
          onPickHistory={(row) => void handlePick(row)}
          onLoadMoreHistory={() => void loadMoreHistory()}
          onRetryHistory={() => void refreshHistory(active?.workdir ?? workdir)}
          onNew={handleNewSameWorkdir}
          onRequestChangeWorkdir={onRequestChangeWorkdir}
        />
        <SessionBanners active={active} activeSid={activeSid} />
        <div
          ref={scrollRef}
          className="cc-view__scroll"
          style={{ "--composer-h": `${composerH}px` } as CSSProperties}
        >
          {active ? (
            <MessageList
              turns={turns}
              streaming={streaming}
              phase={phase}
              stickyRef={stickyRef}
              onRetryLast={handleRetryLast}
              onAnswer={(answers) => void answerElicit(answers)}
              onCancel={() => void cancelElicit()}
              onApprovalDecision={(requestId, decision) =>
                void answerApproval(requestId, decision)
              }
              onRevert={(t) => setRevertTarget(t)}
              workdir={active?.workdir ?? workdir}
              runtime={active.runtime}
            />
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
        <div ref={composerRef}>
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
            onRetryCodexCatalog={loadCodexModels}
            onSend={(text) => {
              void send(text);
              requestAnimationFrame(() => jumpToBottom());
            }}
            onInterrupt={() => void interrupt()}
            onUpdateSettings={(model, nextEffort) =>
              void updateSessionSettings(model, nextEffort)
            }
            onRequestModelPicker={() => setShowModelPicker(true)}
            onRequestEffortPicker={() => setShowEffortPicker(true)}
          />
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
      </div>
      <TodoBar />
    </div>
  );
}
