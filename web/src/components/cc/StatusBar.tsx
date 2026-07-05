import type { Phase, SessionMeta } from "../../stores/ccStore";

/**
 * Top status bar: session name/model/effort/cost/current phase + interrupt.
 * Sits above the message list; `role="status"` so phase changes are announced.
 *
 * Colors follow the spec's semantic map: in-flight (thinking/tool/retrying/
 * compacting) = sunshine; done/finished = garden-green; interrupted = neutral;
 * stalled = warning; error = red. All via tokens — no hardcoded values.
 */
interface StatusBarProps {
  readonly phase: Phase;
  readonly meta: SessionMeta;
  readonly effort: string | null;
  readonly streaming: boolean;
  readonly onInterrupt: () => void;
}

const PHASE_LABEL: Record<Phase, string> = {
  idle: "空闲",
  awaiting_first: "等待 CC 接手…",
  thinking: "思考中",
  generating: "生成中",
  tool: "执行工具",
  retrying: "重试中",
  compacting: "压缩上下文中",
  awaiting_input: "等你回答",
  done: "完成",
  error: "出错",
  interrupted: "已中断",
};

function phaseClass(phase: Phase): string {
  if (phase === "error") return "cc-status__phase--error";
  if (phase === "done" || phase === "idle" || phase === "interrupted")
    return "cc-status__phase--neutral";
  return "cc-status__phase--sunshine"; // in-flight
}

export function StatusBar({
  phase,
  meta,
  effort,
  streaming,
  onInterrupt,
}: StatusBarProps) {
  const cost = meta.costUsd !== null ? `$${meta.costUsd.toFixed(4)}` : null;
  const turns = meta.numTurns !== null ? `${meta.numTurns} 轮` : null;

  return (
    <div className="cc-status" role="status">
      <div className="cc-status__left">
        <span className="cc-status__model">
          {meta.model ?? "未连接"}
          {meta.model && (
            <span className="cc-status__effort">
              · effort: {effort ?? "跟随设置"}
            </span>
          )}
        </span>
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
        {(cost || turns) && (
          <span className="cc-status__accounting">
            {turns}
            {turns && cost && " · "}
            {cost}
          </span>
        )}
        <span className={`cc-status__phase ${phaseClass(phase)}`}>
          {PHASE_LABEL[phase]}
        </span>
        {streaming && (
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
