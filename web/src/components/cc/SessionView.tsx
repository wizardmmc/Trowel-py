import { useEffect, useState, type CSSProperties } from "react";

import type { Turn } from "../../stores/ccStore";
import {
  useCcStore,
  useActiveSession,
} from "../../stores/ccStore";
import { listModels, listSlashItems } from "../../api/cc";
import type { ModelOption, SlashItem } from "../../api/cc";
import { Composer } from "./Composer";
import { EffortPicker } from "./EffortPicker";
import { MessageList } from "./MessageList";
import { ModelPicker } from "./ModelPicker";
import { MultiSessionBar } from "./MultiSessionBar";
import { RevertConfirmModal } from "./RevertConfirmModal";
import { SessionSwitcher } from "./SessionSwitcher";
import { StatusBar } from "./StatusBar";
import { TodoBar } from "./TodoBar";
import { useElementHeight } from "./useElementHeight";
import "./cc.css";

/**
 * slice-028: three-column CC view — left MultiSessionBar | center message area
 * | right TodoBar. The store holds every live session in memory (Q4: 切换不
 * abort); `useActiveSession()` is the bound-to-the-center state.
 *
 * workdir is a parameter (the workdir-picker slice owns choosing it). On mount
 * / workdir change we open a fresh CC session and prime the history switcher;
 * the user can then type (live stream), open more sessions via the multi-bar,
 * or resume a past session from the history dropdown.
 *
 * When activeSid is null (e.g. the active session just /exit'd and the shell
 * unset it) the center shows a "pick a session" prompt instead of an empty
 * pane; the multi-bar rows re-activate on click.
 */
interface SessionViewProps {
  readonly workdir: string;
  /** slice-027: fired when the user clicks the workdir button (or the multi-bar
   * "⇄" affordance) to open the WorkdirPicker. Omit = hide (workdir fixed). */
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
  // slice-028: state is the active session's slice (null when none active).
  const active = useActiveSession();
  // actions pulled via stable selectors (no rerender on background sessions).
  const startSession = useCcStore((s) => s.startSession);
  const loadHistoryIntoView = useCcStore((s) => s.loadHistoryIntoView);
  const send = useCcStore((s) => s.send);
  const interrupt = useCcStore((s) => s.interrupt);
  const answerElicit = useCcStore((s) => s.answerElicit);
  const cancelElicit = useCcStore((s) => s.cancelElicit);
  const revertTurn = useCcStore((s) => s.revertTurn);
  const activeSid = useCcStore((s) => s.activeSid);
  const history = useCcStore((s) => s.history);
  const historyTotal = useCcStore((s) => s.historyTotal);
  const loadingHistory = useCcStore((s) => s.loadingHistory);
  const refreshHistory = useCcStore((s) => s.refreshHistory);

  // slice-026: the turn pending a revert confirmation (null = modal closed).
  const [revertTarget, setRevertTarget] = useState<Turn | null>(null);
  // slice-027: slash items for `/` autocomplete + models for /model picker.
  const [slashItems, setSlashItems] = useState<readonly SlashItem[]>([]);
  const [models, setModels] = useState<readonly ModelOption[]>([]);
  const [showModelPicker, setShowModelPicker] = useState(false);
  const [showEffortPicker, setShowEffortPicker] = useState(false);
  // slice-032: mirror the Composer's live height into --composer-h so
  // .cc-view__scroll's scroll-padding-bottom tracks it — the ✻ thinking row
  // stays visible above the Composer without a hardcoded constant that drifts
  // if the Composer's size changes.
  const [composerRef, composerH] = useElementHeight<HTMLDivElement>();

  const phase = active?.phase ?? "idle";
  const turns = active?.turns ?? [];
  const meta = active?.meta ?? null;
  const effort = active?.effort ?? null;
  const revertEnabled = active?.revertEnabled ?? false;
  const streaming = ACTIVE_PHASES.has(phase);
  // slice-034 feat 3: 当前 model 的 alias（从 meta.model 匹配 models），null → chip 显示默认回退。
  const modelAlias = (() => {
    if (!meta?.model) return null;
    const found = models.find(
      (m) => m.value === meta.model || m.real_model === meta.model,
    );
    return found?.value ?? null;
  })();

  // Drop a stale revert target if the session changes (reset / new / pick) so
  // we never POST a turn_id from one session to another. This is the canonical
  // "reset derived state when an id prop changes" pattern (the lostTurns lookup
  // below is the belt; this is the suspenders that hides the modal immediately).
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setRevertTarget(null);
  }, [activeSid]);

  // workdir 变化时，若当前 active 的 workdir 与新 prop 不一致，用新 workdir 新建会话。
  // temp 会被 dropTempActive 自动丢弃；进行中的对话（connected）保留为多开。
  // 修复 bug1：切换路径后主视图立即切到新路径（不再因已有 temp 而跳过新建）。
  useEffect(() => {
    void (async () => {
      const store = useCcStore.getState();
      // slice-028 v2: reconcile with the backend _REGISTRY first so a page
      // refresh doesn't orphan live cc processes.
      await store.refreshActiveSessions();
      const active0 =
        useCcStore.getState().sessions[useCcStore.getState().activeSid ?? ""];
      if (!active0 || active0.workdir !== workdir) {
        await store.startSession({ workdir });
      }
      // 兜底刷新历史到 prop workdir：覆盖 startSession 失败（active 仍为 null，
      // [active?.workdir] effect 的守卫会跳过）的窄场景，保证 mount/换路径时
      // 历史下拉框至少刷到当前 workdir，不停留在旧路径。
      void store.refreshHistory(workdir);
    })();
  }, [workdir]);

  // 历史列表跟随主视图当前会话的 workdir：切换路径 / 点+ / 多开栏切换，
  // 只要 active 的 workdir 变了就刷新（修复"切换后历史还是旧的"）。
  useEffect(() => {
    if (active?.workdir) {
      void refreshHistory(active.workdir);
    }
  }, [active?.workdir, refreshHistory]);

  // slice-028 v2: a connected session with empty turns needs its jsonl history
  // loaded — happens after refresh (reconcile sets activeSid to a session whose
  // turns were wiped from frontend memory) or when activating a reconciled row
  // via the multi-session bar. Skip live sessions (turns present / mid-stream).
  useEffect(() => {
    if (active && active.connected && active.turns.length === 0 && !active.abort) {
      void loadHistoryIntoView();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeSid]);

  // slice-027: load slash items (project .claude/ depends on workdir) + models
  // (settings.json env). Fail-soft to empty — autocomplete falls back to cc
  // init's bare names; model picker just shows nothing.
  useEffect(() => {
    let cancelled = false;
    Promise.all([listSlashItems(workdir), listModels()])
      .then(([items, m]) => {
        if (cancelled) return;
        setSlashItems(items);
        setModels(m);
      })
      .catch(() => {
        if (cancelled) return;
        setSlashItems([]);
        setModels([]);
      });
    return () => {
      cancelled = true;
    };
  }, [workdir]);

  async function handlePick(ccSessionId: string) {
    const session = await startSession({ workdir, resume_from: ccSessionId });
    if (session) {
      await loadHistoryIntoView();
    }
  }

  function handleNewSameWorkdir() {
    void startSession({ workdir });
  }

  function handleRetryLast() {
    // Re-send the user text of the last turn (best-effort: turn may be mid-error).
    const last = turns[turns.length - 1];
    if (last) {
      void send(last.userText);
    }
  }

  // slice-026: turns lost if we revert to revertTarget (it + every later turn).
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
        <div className="cc-view__top">
          <StatusBar
            phase={phase}
            meta={
              meta
                ? {
                    ...meta,
                    // normalize alias (opus/sonnet/haiku from /model) → real model id
                    model:
                      models.find(
                        (m) => m.value === meta.model || m.real_model === meta.model,
                      )?.real_model ?? meta.model,
                  }
                : {
                    model: null,
                    ccSessionId: null,
                    costUsd: null,
                    numTurns: null,
                    hookFired: null,
                    thinkingStartedAt: null,
                    thinkingTokens: null,
                    stallWarning: null,
                    exited: false,
                    exitReturncode: null,
                  }
            }
            streaming={streaming}
            onInterrupt={() => void interrupt()}
          />
          <SessionSwitcher
            history={history}
            total={historyTotal}
            loading={loadingHistory}
            onPick={(id) => void handlePick(id)}
            onNew={handleNewSameWorkdir}
          />
          {onRequestChangeWorkdir && (() => {
            // slice-028 v2: 显示当前活跃 session 的 workdir（多 session 下每个
            // session 有自己的 workdir），不是 App 传入的 prop（prop 是"新开
            // session 的默认 workdir"）。刷新后 reconcile 来的 session 也走这里。
            const wd = active?.workdir ?? workdir;
            return (
              <button
                type="button"
                className="cc-workdir-btn"
                onClick={onRequestChangeWorkdir}
                title={`工作目录：${wd}（点击切换）`}
              >
                <svg viewBox="0 0 24 24" aria-hidden="true">
                  <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
                </svg>
                {wd.split("/").pop() || wd}
              </button>
            );
          })()}
        </div>
        {!revertEnabled && activeSid && (
          <div className="cc-nogit-banner">
            <span aria-hidden>⚠</span>
            此目录不是 git 仓库，不支持回滚（聊天、历史等其他功能正常）。
          </div>
        )}
        <div
          className="cc-view__scroll"
          style={{ "--composer-h": `${composerH}px` } as CSSProperties}
        >
          {active ? (
            <MessageList
              turns={turns}
              streaming={streaming}
              phase={phase}
              onRetryLast={handleRetryLast}
              onAnswer={(answers) => void answerElicit(answers)}
              onCancel={() => void cancelElicit()}
              onRevert={(t) => setRevertTarget(t)}
              workdir={active?.workdir ?? workdir}
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
        <div ref={composerRef}>
          <Composer
            streaming={streaming}
            disabled={!activeSid || phase === "awaiting_input"}
            awaitingInput={phase === "awaiting_input"}
            onSend={(text) => void send(text)}
            onInterrupt={() => void interrupt()}
            slashItems={slashItems}
            models={models}
            currentModelAlias={modelAlias}
            currentEffort={effort}
            onPickModel={(v) => void send(`/model ${v}`)}
            onPickEffort={(v) => void send(`/effort ${v}`)}
            onRequestModelPicker={() => setShowModelPicker(true)}
            onRequestEffortPicker={() => setShowEffortPicker(true)}
          />
        </div>
        {revertTarget && (
          <RevertConfirmModal
            lostTurns={lostTurns}
            onConfirm={() => void handleRevertConfirm()}
            onCancel={() => setRevertTarget(null)}
          />
        )}
        {/* slice-034 feat 3: 双入口并存——bare `/model` `/effort` slash 走这个居中 modal；
            Composer 底栏 chip 走 popover。两条路径都能选 model/effort（有意保留）。 */}
        {showModelPicker && (
          <ModelPicker
            models={models}
            currentModel={
              meta
                ? models.find(
                    (m) => m.real_model === meta.model || m.value === meta.model,
                  )?.value ?? meta.model ?? ""
                : ""
            }
            onSelect={(v) => {
              void send(`/model ${v}`);
              setShowModelPicker(false);
            }}
            onCancel={() => setShowModelPicker(false)}
          />
        )}
        {showEffortPicker && (
          <EffortPicker
            currentEffort={effort}
            onSelect={(v) => {
              void send(`/effort ${v}`);
              setShowEffortPicker(false);
            }}
            onCancel={() => setShowEffortPicker(false)}
          />
        )}
      </div>
      <TodoBar />
    </div>
  );
}
