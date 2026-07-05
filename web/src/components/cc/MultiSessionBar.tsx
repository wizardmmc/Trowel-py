import { useCcStore } from "../../stores/ccStore";
import {
  MAX_RUNNING,
  MAX_CONNECTIONS,
  type PerSessionState,
} from "../../stores/ccStore";

/**
 * slice-028 D1 MultiSessionBar — the left column. Lists only **live
 * connections** (sessions with a live cc subprocess = `connected && !exited`).
 *
 * Per the user's terminal metaphor: a session is a "connection" only once the
 * user has sent a message (which spawns cc). "+" / load-history states never
 * appear here, and an exited session (× close or `/exit`) is removed entirely
 * — never greyed/resumable in this list (reload history to view it again).
 *
 * Row state:
 *  - running (abort set, in-turn)      → yellow pulsing dot
 *  - connected idle (live, not in-turn) → green dot
 *
 * Each row has a × close button (DELETE the session + drop it). The footer
 * shows the live resource caps (Q5'): connected/20 · running/5.
 */
interface MultiSessionBarProps {
  /** Fired when the user clicks "+ 同目录" (prepare a new chat in the active workdir). */
  readonly onNewSameWorkdir: () => void;
  /** Fired when the user clicks "⇄" (open the workdir picker). */
  readonly onChangeWorkdir: () => void;
}

/** Pick the dot class for a session's turn state (connected only — exited rows
 * are filtered out before render). */
function dotClass(s: PerSessionState): string {
  if (s.abort !== null) return "cc-multibar__dot--running";
  return "cc-multibar__dot--idle";
}

/** One-line status subtitle (model · state). */
function statusText(s: PerSessionState): string {
  if (s.abort !== null) {
    const phase =
      s.phase === "thinking"
        ? "思考中"
        : s.phase === "tool"
          ? "跑工具"
          : "生成中";
    return `${s.meta.model ?? "model"} · ${phase}`;
  }
  return `${s.meta.model ?? "model"} · idle`;
}

export function MultiSessionBar({
  onNewSameWorkdir,
  onChangeWorkdir,
}: MultiSessionBarProps) {
  const sessions = useCcStore((s) => s.sessions);
  const activeSid = useCcStore((s) => s.activeSid);
  const activate = useCcStore((s) => s.activateSession);
  const close = useCcStore((s) => s.closeSession);

  // slice-028 v2: only live connections appear (connected && !exited).
  const connected = Object.entries(sessions).filter(
    ([, s]) => s.connected && !s.meta.exited,
  );
  const running = connected.filter(([, s]) => s.abort !== null).length;
  const connections = connected.length;
  const connectionFull = connections >= MAX_CONNECTIONS;

  // Display order: active first, then others — stable by sid otherwise.
  const ordered = connected.sort(([aId], [bId]) => {
    if (aId === activeSid) return -1;
    if (bId === activeSid) return 1;
    return aId < bId ? -1 : 1;
  });

  return (
    <aside className="cc-multibar" aria-label="多开会话">
      <div className="cc-multibar__head">
        <span className="cc-multibar__title">多开</span>
        <button
          type="button"
          className="cc-multibar__btn cc-multibar__btn--primary"
          onClick={onNewSameWorkdir}
          title="同目录新开"
          aria-label="同目录新开"
        >
          +
        </button>
        <button
          type="button"
          className="cc-multibar__btn"
          onClick={onChangeWorkdir}
          title="换目录新开"
          aria-label="换目录新开"
        >
          ⇄
        </button>
      </div>
      <div className="cc-multibar__list">
        {ordered.length === 0 && (
          <div className="cc-multibar__empty">
            暂无连接
            <br />
            <span className="cc-multibar__empty-hint">
              发消息或加载历史后会出现在这里
            </span>
          </div>
        )}
        {ordered.map(([sid, s]) => {
          const isActive = sid === activeSid;
          return (
            <div
              key={sid}
              className={
                "cc-multibar__item" +
                (isActive ? " cc-multibar__item--active" : "")
              }
            >
              <button
                type="button"
                className="cc-multibar__main"
                onClick={() => void activate(sid)}
                title={s.workdir}
              >
                <span className="cc-multibar__row1">
                  <span
                    className={"cc-multibar__dot " + dotClass(s)}
                    aria-hidden="true"
                  />
                  <span className="cc-multibar__name">{s.name}</span>
                </span>
                <span className="cc-multibar__row2">{statusText(s)}</span>
              </button>
              <button
                type="button"
                className="cc-multibar__close"
                onClick={() => void close(sid)}
                title="关闭"
                aria-label={`关闭 ${s.name}`}
              >
                ×
              </button>
            </div>
          );
        })}
      </div>
      <div className="cc-multibar__foot">
        <span className={running >= MAX_RUNNING ? "cc-multibar__foot--warn" : ""}>
          {running}/{MAX_RUNNING} 在跑
        </span>
        {" · "}
        <span className={connectionFull ? "cc-multibar__foot--warn" : ""}>
          {connections}/{MAX_CONNECTIONS} 连接
        </span>
      </div>
    </aside>
  );
}
