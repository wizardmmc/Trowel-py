/** 展示当前连接的用户会话，并提供切换、重命名和关闭操作。 */

import { useState, type FormEvent } from "react";

import { useAgentStore } from "../../agent/application";
import { useAgentStoreFrameSelector } from "../../agent/application";
import {
  MAX_RUNNING,
  MAX_CONNECTIONS,
  type PerSessionState,
} from "../../agent/application";
import { getRuntimePresentation } from "../../agent/runtimes";

interface MultiSessionBarProps {
  readonly onNewSameWorkdir: () => void;
  readonly onChangeWorkdir: () => void;
}

const selectSessions = (state: ReturnType<typeof useAgentStore.getState>) =>
  state.sessions;

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

function workdirName(workdir: string): string {
  const normalized = workdir.replace(/[\\/]+$/, "");
  return normalized.split(/[\\/]/).pop() || workdir;
}

function sessionTitle(s: PerSessionState): string {
  return s.displayTitle || "新会话";
}

export function MultiSessionBar({
  onNewSameWorkdir,
  onChangeWorkdir,
}: MultiSessionBarProps) {
  const sessions = useAgentStoreFrameSelector(selectSessions);
  const activeSid = useAgentStore((s) => s.activeSid);
  const activate = useAgentStore((s) => s.activateSession);
  const close = useAgentStore((s) => s.closeSession);
  const rename = useAgentStore((s) => s.renameSessionTitle);
  const [editingSid, setEditingSid] = useState<string | null>(null);
  const [draft, setDraft] = useState("");

  const connected = Object.entries(sessions).filter(
    ([, s]) =>
      s.sessionKind !== "delegate" && s.connected && !s.meta.exited,
  );
  const running = connected.filter(([, s]) => s.abort !== null).length;
  const connections = connected.length;
  const connectionFull = connections >= MAX_CONNECTIONS;

  const byWorkdir = new Map<
    string,
    Array<[string, PerSessionState]>
  >();
  for (const entry of connected) {
    const workdir = entry[1].workdir;
    const group = byWorkdir.get(workdir);
    if (group) group.push(entry);
    else byWorkdir.set(workdir, [entry]);
  }
  const groups = [...byWorkdir.entries()];

  function beginRename(sid: string, s: PerSessionState): void {
    setEditingSid(sid);
    setDraft(sessionTitle(s));
  }

  function submitRename(event: FormEvent<HTMLFormElement>, sid: string): void {
    event.preventDefault();
    const title = draft.trim();
    if (!title) return;
    setEditingSid(null);
    void rename(sid, title);
  }

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
        {connected.length === 0 && (
          <div className="cc-multibar__empty">
            暂无连接
            <br />
            <span className="cc-multibar__empty-hint">
              发消息或加载历史后会出现在这里
            </span>
          </div>
        )}
        {groups.map(([workdir, entries], groupIndex) => {
          const labelId = `cc-workdir-${groupIndex}`;
          return (
            <section
              key={workdir}
              className="cc-multibar__group"
              role="group"
              aria-labelledby={labelId}
            >
              <div
                id={labelId}
                className="cc-multibar__group-title"
                title={workdir}
              >
                {workdirName(workdir)}
              </div>
              {entries.map(([sid, s]) => {
                const isActive = sid === activeSid;
                const title = sessionTitle(s);
                const presentation = getRuntimePresentation(
                  s.runtime,
                  s.capabilities,
                );
                return (
                  <div
                    key={sid}
                    className={
                      "cc-multibar__item" +
                      (isActive ? " cc-multibar__item--active" : "")
                    }
                  >
                    {editingSid === sid ? (
                      <form
                        className="cc-multibar__editor"
                        onSubmit={(event) => submitRename(event, sid)}
                      >
                        <input
                          autoFocus
                          aria-label="会话标题"
                          maxLength={80}
                          value={draft}
                          onChange={(event) => setDraft(event.target.value)}
                        />
                        <button type="submit" aria-label="保存标题">
                          ✓
                        </button>
                        <button
                          type="button"
                          aria-label="取消改名"
                          onClick={() => setEditingSid(null)}
                        >
                          ×
                        </button>
                      </form>
                    ) : (
                      <>
                        <button
                          type="button"
                          className="cc-multibar__main"
                          onClick={() => void activate(sid)}
                          title={title}
                        >
                          <span className="cc-multibar__row1">
                            <span
                              className={"cc-multibar__dot " + dotClass(s)}
                              aria-hidden="true"
                            />
                            <span
                              className="cc-multibar__name"
                              data-testid="session-title"
                            >
                              {title}
                            </span>
                            <span
                              className={`cc-runtime-badge cc-runtime-badge--${s.runtime}`}
                              title={`${presentation.label} runtime`}
                            >
                              {presentation.shortLabel}
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
                                <span className="cc-multibar__perm">
                                  {s.permission}
                                </span>
                              </>
                            )}
                          </span>
                        </button>
                        <div className="cc-multibar__actions">
                          <button
                            type="button"
                            className="cc-multibar__action"
                            onClick={() => beginRename(sid, s)}
                            title="重命名"
                            aria-label={`重命名 ${title}`}
                          >
                            ✎
                          </button>
                          <button
                            type="button"
                            className="cc-multibar__action cc-multibar__action--close"
                            onClick={() => void close(sid)}
                            title="关闭"
                            aria-label={`关闭 ${title}`}
                          >
                            ×
                          </button>
                        </div>
                      </>
                    )}
                  </div>
                );
              })}
            </section>
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
