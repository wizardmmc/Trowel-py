/** 把当前会话的异常、恢复状态和能力缺口收敛成一张可操作通知。 */

import { useEffect, useRef, useState } from "react";

import {
  copySessionDiagnostic,
  type PerSessionState,
} from "../../agent/application";
import { getRuntimePresentation } from "../../agent/runtimes";
import { CopyButton } from "../ui/CopyButton";
import { RateLimitBanner } from "./RateLimitBanner";
import {
  collectSessionIssues,
  safeSessionAction,
  type SessionIssue,
} from "./sessionIssuePresentation";

interface SessionBannersProps {
  readonly active: PerSessionState | null;
  readonly activeSid: string | null;
  readonly onRetryClose?: () => void;
}

export function SessionBanners({
  active,
  activeSid,
  onRetryClose,
}: SessionBannersProps) {
  const presentation = active
    ? getRuntimePresentation(active.runtime, active.capabilities)
    : null;
  const issues = active && presentation
    ? collectSessionIssues(active, activeSid, presentation)
    : [];
  const diagnosticKey = active && issues.length > 0
    ? [
        activeSid ?? "unknown",
        active.stateGeneration,
        active.transportProblem?.code ?? "state",
        active.transportProblem?.occurredAt ?? "current",
        ...issues.map((issue) => issue.id),
      ].join(":")
    : null;
  const [expandedProblemKey, setExpandedProblemKey] = useState<string | null>(
    null,
  );
  const [copiedProblemKey, setCopiedProblemKey] = useState<string | null>(null);
  const copyResetTimer = useRef<number | null>(null);

  useEffect(
    () => () => {
      if (copyResetTimer.current !== null) {
        window.clearTimeout(copyResetTimer.current);
      }
    },
    [],
  );

  async function handleCopyDiagnostic(): Promise<void> {
    if (!active || !diagnosticKey) return;
    if (
      !(await copySessionDiagnostic(active, {
        visibleIssueIds: issues.map((issue) => issue.id),
        missingCapabilities: presentation?.missingCapabilities ?? [],
      }))
    ) {
      return;
    }
    setCopiedProblemKey(diagnosticKey);
    if (copyResetTimer.current !== null) {
      window.clearTimeout(copyResetTimer.current);
    }
    copyResetTimer.current = window.setTimeout(
      () => setCopiedProblemKey(null),
      1300,
    );
  }

  return (
    <>
      {active && issues.length > 0 && (
        <SessionIncidentNotice
          active={active}
          issues={issues}
          expanded={
            diagnosticKey !== null && expandedProblemKey === diagnosticKey
          }
          copied={diagnosticKey !== null && copiedProblemKey === diagnosticKey}
          onToggleDetails={
            active.transportProblem && diagnosticKey
              ? () =>
                  setExpandedProblemKey((current) =>
                    current === diagnosticKey ? null : diagnosticKey,
                  )
              : undefined
          }
          onCopy={
            diagnosticKey ? () => void handleCopyDiagnostic() : undefined
          }
          onRetryClose={
            active.resourceState === "needs_reconcile"
              ? onRetryClose
              : undefined
          }
        />
      )}
      <RateLimitBanner snapshot={active?.meta.rateLimit ?? null} />
    </>
  );
}

function SessionIncidentNotice({
  active,
  issues,
  expanded,
  copied,
  onToggleDetails,
  onCopy,
  onRetryClose,
}: {
  readonly active: PerSessionState;
  readonly issues: readonly SessionIssue[];
  readonly expanded: boolean;
  readonly copied: boolean;
  readonly onToggleDetails?: () => void;
  readonly onCopy?: () => void;
  readonly onRetryClose?: () => void;
}) {
  const multiple = issues.length > 1;
  const highestTone = issues.some((issue) => issue.tone === "error")
    ? "error"
    : issues.some((issue) => issue.tone === "warning")
      ? "warning"
      : "info";
  const role = issues.some((issue) => issue.role === "alert")
    ? "alert"
    : "status";
  const primary = issues[0];
  const problem = active.transportProblem;
  const title = multiple
    ? `会话有 ${issues.length} 项状态需要留意`
    : primary.title;
  const detail = multiple
    ? "这些状态彼此独立，已集中展示，避免连续堆叠提示。"
    : primary.detail;

  return (
    <section
      className={`cc-session-notice cc-session-notice--${multiple ? "grouped" : highestTone}${multiple && highestTone === "error" ? " cc-session-notice--has-error" : ""}`}
      role={role}
    >
      <div className="cc-session-notice__main">
        <span className="cc-session-notice__icon" aria-hidden="true">
          {multiple ? issues.length : highestTone === "error" ? "×" : "!"}
        </span>
        <div className="cc-session-notice__copy">
          <b>{title}</b>
          <span>{detail}</span>
          {multiple && (
            <div className="cc-session-notice__issues">
              {issues.map((issue) => (
                <div className="cc-session-notice__issue" key={issue.id}>
                  <i data-tone={issue.tone} aria-hidden="true" />
                  <strong>{issue.title}</strong>
                  <span>{issue.detail}</span>
                </div>
              ))}
            </div>
          )}
        </div>
        <div className="cc-session-notice__actions">
          {onRetryClose && (
            <button
              type="button"
              className="cc-session-notice__primary-action"
              onClick={onRetryClose}
            >
              重试关闭
            </button>
          )}
          {onCopy && (
            <CopyButton
              tone="neutral"
              copied={copied}
              onClick={onCopy}
              ariaLabel="复制会话诊断"
            />
          )}
        </div>
      </div>
      {problem && onToggleDetails && (
        <>
          <div className="cc-session-notice__footer">
            <button
              type="button"
              className="cc-session-notice__details-toggle"
              aria-expanded={expanded}
              onClick={onToggleDetails}
            >
              技术详情
              <span aria-hidden="true">⌄</span>
            </button>
            <span>诊断信息不包含会话正文</span>
          </div>
          {expanded && (
            <div className="cc-session-notice__details">
              <DiagnosticFact
                label="来源 / 操作"
                value={`${problem.status === null ? "renderer" : "sidecar"} · ${problem.operation}`}
              />
              <DiagnosticFact
                label="问题 / 时间"
                value={`${problem.code} · ${new Date(problem.occurredAt).toLocaleTimeString()}`}
              />
              <DiagnosticFact
                label="当前事实"
                value={`resource=${active.resourceState} · turn=${active.turnState} · live=${active.liveState}`}
              />
              <DiagnosticFact
                label="安全动作"
                value={safeSessionAction(problem.code)}
              />
            </div>
          )}
        </>
      )}
    </section>
  );
}

function DiagnosticFact({
  label,
  value,
}: {
  readonly label: string;
  readonly value: string;
}) {
  return (
    <div>
      <span>{label}</span>
      <b>{value}</b>
    </div>
  );
}
