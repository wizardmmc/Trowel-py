import { useEffect, useState } from "react";

import type { Turn } from "../../stores/ccStore";
import { useCcStore } from "../../stores/ccStore";
import { listModels, listSlashItems } from "../../api/cc";
import type { ModelOption, SlashItem } from "../../api/cc";
import { Composer } from "./Composer";
import { EffortPicker } from "./EffortPicker";
import { MessageList } from "./MessageList";
import { ModelPicker } from "./ModelPicker";
import { RevertConfirmModal } from "./RevertConfirmModal";
import { StatusBar } from "./StatusBar";
import { SessionSwitcher } from "./SessionSwitcher";
import "./cc.css";

/**
 * The CC session view — status bar + history switcher + message list + composer.
 *
 * workdir is a parameter (the workdir-picker slice owns choosing it). On mount
 * we open a fresh CC session and prime the history switcher; the user can then
 * either type (live stream) or pick a past session from the switcher (resume
 * + history replay through the same reducer).
 *
 * Streaming phase is "in-flight" — anything not idle/done/error/interrupted.
 * The composer is disabled until a session id exists.
 */
interface SessionViewProps {
  readonly workdir: string;
  /** slice-027: fired when the user clicks the workdir button to open the
   * WorkdirPicker. Omit = hide the button (workdir is fixed). */
  readonly onRequestChangeWorkdir?: () => void;
}

const ACTIVE_PHASES = new Set([
  "awaiting_first",
  "thinking",
  "generating",
  "tool",
  "retrying",
  "compacting",
  "stalled",
]);

export function SessionView({
  workdir,
  onRequestChangeWorkdir,
}: SessionViewProps) {
  const {
    phase,
    meta,
    effort,
    turns,
    sessionId,
    history,
    historyTotal,
    loadingHistory,
    revertEnabled,
    startSession,
    refreshHistory,
    loadHistoryIntoView,
    send,
    interrupt,
    answerElicit,
    cancelElicit,
    revertTurn,
  } = useCcStore();

  // slice-026: the turn pending a revert confirmation (null = modal closed).
  const [revertTarget, setRevertTarget] = useState<Turn | null>(null);
  // slice-027: slash items for `/` autocomplete + models for /model picker.
  const [slashItems, setSlashItems] = useState<readonly SlashItem[]>([]);
  const [models, setModels] = useState<readonly ModelOption[]>([]);
  const [showModelPicker, setShowModelPicker] = useState(false);
  const [showEffortPicker, setShowEffortPicker] = useState(false);

  // Drop a stale revert target if the session changes (reset / new / pick) so
  // we never POST a turn_id from one session to another.
  useEffect(() => {
    setRevertTarget(null);
  }, [sessionId]);

  // Open a fresh session + prime history list on mount / when workdir changes.
  useEffect(() => {
    void startSession({ workdir });
    void refreshHistory(workdir);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workdir]);

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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workdir]);

  const streaming = ACTIVE_PHASES.has(phase);

  async function handlePick(ccSessionId: string) {
    const session = await startSession({ workdir, resume_from: ccSessionId });
    if (session) {
      await loadHistoryIntoView();
    }
  }

  function handleNew() {
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
  // Match by id (stable) — reference equality would miss if the reducer
  // rebuilt the target object, and slice(-1) on a -1 indexOf would wrongly
  // list every turn as lost.
  const lostTurns = (() => {
    if (!revertTarget) return [];
    const idx = turns.findIndex((t) => t.id === revertTarget.id);
    return idx === -1 ? [] : turns.slice(idx);
  })();

  async function handleRevertConfirm() {
    if (!revertTarget?.turnId) return;
    if (lostTurns.length === 0) {
      // target vanished (session changed) — just close
      setRevertTarget(null);
      return;
    }
    const turnId = revertTarget.turnId;
    setRevertTarget(null);
    await revertTurn(turnId);
  }

  return (
    <div className="cc-view">
      <div className="cc-view__top">
        <StatusBar
          phase={phase}
          meta={{
            ...meta,
            // normalize alias (opus/sonnet/haiku from /model) → real model id
            // (glm-5.2[1M] / glm-5.1) for consistent display with session_started
            model: models.find(
              (m) => m.value === meta.model || m.real_model === meta.model,
            )?.real_model ?? meta.model,
          }}
          effort={effort}
          streaming={streaming}
          onInterrupt={() => void interrupt()}
        />
        <SessionSwitcher
          history={history}
          total={historyTotal}
          loading={loadingHistory}
          onPick={(id) => void handlePick(id)}
          onNew={handleNew}
        />
        {onRequestChangeWorkdir && (
          <button
            type="button"
            className="cc-workdir-btn"
            onClick={onRequestChangeWorkdir}
            title={`工作目录：${workdir}（点击切换）`}
          >
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
            </svg>
            {workdir.split("/").pop() || workdir}
          </button>
        )}
      </div>
      {!revertEnabled && sessionId && (
        <div className="cc-nogit-banner">
          <span aria-hidden>⚠</span>
          此目录不是 git 仓库，不支持回滚（聊天、历史等其他功能正常）。
        </div>
      )}
      <div className="cc-view__scroll">
        <MessageList
          turns={turns}
          streaming={streaming}
          onRetryLast={handleRetryLast}
          onAnswer={(answers) => void answerElicit(answers)}
          onCancel={() => void cancelElicit()}
          onRevert={(t) => setRevertTarget(t)}
        />
      </div>
      <Composer
        streaming={streaming}
        disabled={!sessionId || phase === "awaiting_input"}
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
            // meta.model may be the real id (from session_started: e.g.
            // 'glm-5.2') or an alias (from model_changed: e.g. 'opus'). Match
            // against either so the picker highlights the right row either way.
            models.find(
              (m) => m.real_model === meta.model || m.value === meta.model,
            )?.value ?? meta.model
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
  );
}
