/** 展示当前会话身份、历史切换入口和运行状态。 */

import type { ModelOption } from "../../api/cc";
import type { AgentHistoryRow } from "../../agent/transport";
import type {
  Phase,
  SessionMeta,
} from "../../agent/domain";
import { SessionIdCopyButton } from "./SessionIdCopyButton";
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
  readonly runtimeLabel: string;
  readonly streaming: boolean;
  readonly models: readonly ModelOption[];
  readonly history: readonly AgentHistoryRow[];
  readonly loadingHistory: boolean;
  readonly loadingMoreHistory: boolean;
  readonly historyHasMore: boolean;
  readonly historyError: string | null;
  readonly workdir: string;
  readonly nativeSessionId: string | null;
  readonly workSummary?: string | null;
  readonly workRailLabel?: string;
  readonly onToggleWorkRail?: () => void;
  readonly onInterrupt?: () => void;
  readonly onPickHistory: (row: AgentHistoryRow) => void;
  readonly onLoadMoreHistory: () => void;
  readonly onRetryHistory: () => void;
  readonly onNew: () => void;
  readonly onRequestChangeWorkdir?: () => void;
}

export function SessionHeader({
  phase,
  meta,
  runtimeLabel,
  streaming,
  models,
  history,
  loadingHistory,
  loadingMoreHistory,
  historyHasMore,
  historyError,
  workdir,
  nativeSessionId,
  workSummary,
  workRailLabel = "打开右栏",
  onToggleWorkRail,
  onInterrupt,
  onPickHistory,
  onLoadMoreHistory,
  onRetryHistory,
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
        runtimeLabel={runtimeLabel}
        streaming={streaming}
        onInterrupt={onInterrupt}
      />
      <SessionSwitcher
        history={history}
        loading={loadingHistory}
        loadingMore={loadingMoreHistory}
        hasMore={historyHasMore}
        error={historyError}
        onLoadMore={onLoadMoreHistory}
        onRetry={onRetryHistory}
        onPick={onPickHistory}
        onNew={onNew}
      />
      {workSummary && onToggleWorkRail && (
        <button
          type="button"
          className="cc-workrail-toggle"
          onClick={onToggleWorkRail}
          aria-label={workRailLabel}
        >
          {workSummary}
        </button>
      )}
      {(nativeSessionId || onRequestChangeWorkdir) && (
        <div className="cc-session-context">
          {nativeSessionId && (
            <SessionIdCopyButton
              key={nativeSessionId}
              sessionId={nativeSessionId}
            />
          )}
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
              <span className="cc-workdir-btn__value">
                {workdir.split("/").pop() || workdir}
              </span>
            </button>
          )}
        </div>
      )}
    </div>
  );
}
