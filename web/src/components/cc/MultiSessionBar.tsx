import { useCcStore } from "../../stores/ccStore";
import {
  MAX_RUNNING,
  MAX_CONNECTIONS,
  type PerSessionState,
} from "../../stores/ccStore";

interface MultiSessionBarProps {
  readonly onNewSameWorkdir: () => void;
  readonly onChangeWorkdir: () => void;
}

function dotClass(s: PerSessionState): string {
  if (s.abort !== null) return "cc-multibar__dot--running";
  return "cc-multibar__dot--idle";
}

function statusText(s: PerSessionState): string {
  if (s.abort !== null) {
    const phase =
      s.phase === "thinking"
        ? "思考中"
        : s.phase === "tool"
          ? "跑工具"
          : s.phase === "background_waiting"
            ? "等后台任务"
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

  const connected = Object.entries(sessions).filter(
    ([, s]) => s.connected && !s.meta.exited,
  );
  const running = connected.filter(([, s]) => s.abort !== null).length;
  const connections = connected.length;
  const connectionFull = connections >= MAX_CONNECTIONS;

  const ordered = [...connected].sort(([aId], [bId]) => {
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
                  <span
                    className={`cc-runtime-badge cc-runtime-badge--${s.runtime}`}
                    title={
                      s.runtime === "codex" ? "Codex runtime" : "Claude Code runtime"
                    }
                  >
                    {s.runtime === "codex" ? "Codex" : "CC"}
                  </span>
                </span>
                <span className="cc-multibar__row2">{statusText(s)}</span>
                <span
                  className="cc-multibar__cond"
                  title="Memory · Profile · 权限"
                >
                  <span
                    className={
                      s.memoryEnabled
                        ? "cc-multibar__cond-on"
                        : "cc-multibar__cond-off"
                    }
                  >
                    M
                  </span>
                  <span className="cc-multibar__cond-sep">·</span>
                  <span
                    className={
                      s.profileEnabled
                        ? "cc-multibar__cond-on"
                        : "cc-multibar__cond-off"
                    }
                  >
                    P
                  </span>
                  {s.permission && (
                    <>
                      <span className="cc-multibar__cond-sep">·</span>
                      <span className="cc-multibar__perm">{s.permission}</span>
                    </>
                  )}
                </span>
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
