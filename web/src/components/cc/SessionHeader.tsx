import type { ModelOption } from "../../api/cc";
import type { AgentHistoryRow } from "../../api/agent";
import type {
  Phase,
  SessionMeta,
} from "../../stores/ccStore";
import { SessionSwitcher } from "./SessionSwitcher";
import { StatusBar } from "./StatusBar";

const EMPTY_META: SessionMeta = {
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
  usage: null,
  hostDegraded: false,
  rateLimit: null,
};

interface SessionHeaderProps {
  readonly phase: Phase;
  readonly meta: SessionMeta | null;
  readonly streaming: boolean;
  readonly models: readonly ModelOption[];
  readonly history: readonly AgentHistoryRow[];
  readonly historyTotal: number;
  readonly loadingHistory: boolean;
  readonly workdir: string;
  readonly onInterrupt: () => void;
  readonly onPickHistory: (row: AgentHistoryRow) => void;
  readonly onNew: () => void;
  readonly onRequestChangeWorkdir?: () => void;
}

export function SessionHeader({
  phase,
  meta,
  streaming,
  models,
  history,
  historyTotal,
  loadingHistory,
  workdir,
  onInterrupt,
  onPickHistory,
  onNew,
  onRequestChangeWorkdir,
}: SessionHeaderProps) {
  const normalizedMeta = meta
    ? {
        ...meta,
        model:
          models.find(
            (model) =>
              model.value === meta.model ||
              model.real_model === meta.model,
          )?.real_model ?? meta.model,
      }
    : EMPTY_META;

  return (
    <div className="cc-view__top">
      <StatusBar
        phase={phase}
        meta={normalizedMeta}
        streaming={streaming}
        onInterrupt={onInterrupt}
      />
      <SessionSwitcher
        history={history}
        total={historyTotal}
        loading={loadingHistory}
        onPick={onPickHistory}
        onNew={onNew}
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
  );
}
