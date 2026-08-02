/** 展示会话阶段、单轮用量、压缩摘要和中断入口。 */

import type { Phase, SessionMeta } from "../../agent/domain";
import { accountingLabel, phaseLabel } from "./statusPresentation";

interface StatusBarProps {
  readonly phase: Phase;
  readonly meta: SessionMeta;
  readonly runtimeLabel: string;
  readonly streaming: boolean;
  readonly onInterrupt?: () => void;
}

function phaseClass(phase: Phase): string {
  if (phase === "error") return "cc-status__phase--error";
  if (phase === "done" || phase === "idle" || phase === "interrupted")
    return "cc-status__phase--neutral";
  return "cc-status__phase--sunshine";
}

export function StatusBar({
  phase,
  meta,
  runtimeLabel,
  streaming,
  onInterrupt,
}: StatusBarProps) {
  const accounting = accountingLabel(meta);

  return (
    <div className="cc-status" role="status">
      <div className="cc-status__left">
        <span className={`cc-status__phase ${phaseClass(phase)}`}>
          {phaseLabel(phase, runtimeLabel)}
        </span>
        {accounting && (
          <>
            <span className="cc-status__separator" aria-hidden="true">
              ·
            </span>
            <span className="cc-status__accounting">{accounting}</span>
          </>
        )}
        {meta.hookFired && (
          <span className="cc-status__hook" title={`hook: ${meta.hookFired}`}>
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M12 3v6a3 3 0 0 0 6 0V9" />
              <circle cx="12" cy="3" r="1" />
            </svg>
            {meta.hookFired}
          </span>
        )}
      </div>
      <div className="cc-status__right">
        {streaming && onInterrupt && (
          <button
            type="button"
            className="cc-status__interrupt"
            onClick={onInterrupt}
          >
            中断
          </button>
        )}
      </div>
    </div>
  );
}
