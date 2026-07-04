import { useEffect } from "react";

import { useCcStore } from "../../stores/ccStore";
import { Composer } from "./Composer";
import { MessageList } from "./MessageList";
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
    startSession,
    refreshHistory,
    loadHistoryIntoView,
    send,
    interrupt,
    answerElicit,
    cancelElicit,
  } = useCcStore();

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
      <div className="cc-view__scroll">
        <MessageList
          turns={turns}
          streaming={streaming}
          onRetryLast={handleRetryLast}
          onAnswer={(answers) => void answerElicit(answers)}
          onCancel={() => void cancelElicit()}
        />
      </div>
      <Composer
        streaming={streaming}
        disabled={!sessionId || phase === "awaiting_input"}
        awaitingInput={phase === "awaiting_input"}
        onSend={(text) => void send(text)}
        onInterrupt={() => void interrupt()}
      />
    </div>
  );
}
