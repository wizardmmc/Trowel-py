import { useEffect, useState } from "react";

import type { Turn } from "../../stores/ccStore";
import { useCcStore } from "../../stores/ccStore";
import { Composer } from "./Composer";
import { MessageList } from "./MessageList";
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

export function SessionView({ workdir }: SessionViewProps) {
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
          meta={meta}
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
      />
      {revertTarget && (
        <RevertConfirmModal
          lostTurns={lostTurns}
          onConfirm={() => void handleRevertConfirm()}
          onCancel={() => setRevertTarget(null)}
        />
      )}
    </div>
  );
}
