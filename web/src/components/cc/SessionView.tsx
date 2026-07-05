import { useEffect, useState } from "react";

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

  // slice-026: the turn pending a revert confirmation (null = modal closed).
  const [revertTarget, setRevertTarget] = useState<Turn | null>(null);
  // slice-027: slash items for `/` autocomplete + models for /model picker.
  const [slashItems, setSlashItems] = useState<readonly SlashItem[]>([]);
  const [models, setModels] = useState<readonly ModelOption[]>([]);
  const [showModelPicker, setShowModelPicker] = useState(false);
  const [showEffortPicker, setShowEffortPicker] = useState(false);

  const phase = active?.phase ?? "idle";
  const turns = active?.turns ?? [];
  const meta = active?.meta ?? null;
  const effort = active?.effort ?? null;
  const revertEnabled = active?.revertEnabled ?? false;
  const streaming = ACTIVE_PHASES.has(phase);

  // Drop a stale revert target if the session changes (reset / new / pick) so
  // we never POST a turn_id from one session to another. This is the canonical
  // "reset derived state when an id prop changes" pattern (the lostTurns lookup
  // below is the belt; this is the suspenders that hides the modal immediately).
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setRevertTarget(null);
  }, [activeSid]);

  // Open a fresh session + prime history list on mount / when workdir changes.
  useEffect(() => {
    void (async () => {
      const store = useCcStore.getState();
      // slice-028 v2: reconcile with the backend _REGISTRY first so a page
      // refresh doesn't orphan live cc processes — if the backend still has
      // sessions, the dict re-absorbs them (× closable) instead of starting
      // a duplicate.
      await store.refreshActiveSessions();
      // only start a fresh temp session when nothing is active after reconcile
      if (!useCcStore.getState().activeSid) {
        await store.startSession({ workdir });
      }
      // history dropdown follows the active session's workdir (after refresh it
      // may be a reconciled session whose workdir ≠ this prop), else this prop.
      const active0 = useCcStore.getState().sessions[useCcStore.getState().activeSid ?? ""];
      void store.refreshHistory(active0?.workdir ?? workdir);
    })();
  }, [workdir]);

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
            effort={effort}
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
        <div className="cc-view__scroll">
          {active ? (
            <MessageList
              turns={turns}
              streaming={streaming}
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
        <Composer
          streaming={streaming}
          disabled={!activeSid || phase === "awaiting_input"}
          awaitingInput={phase === "awaiting_input"}
          onSend={(text) => void send(text)}
          onInterrupt={() => void interrupt()}
          slashItems={slashItems}
          onRequestModelPicker={() => setShowModelPicker(true)}
          onRequestEffortPicker={() => setShowEffortPicker(true)}
        />
        {revertTarget && (
          <RevertConfirmModal
            lostTurns={lostTurns}
            onConfirm={() => void handleRevertConfirm()}
            onCancel={() => setRevertTarget(null)}
          />
        )}
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
