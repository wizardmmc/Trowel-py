/** 收集工作区、同步轮次模式和两至八位可独立配置的参与者草稿。 */

import { useState } from "react";
import { createPortal } from "react-dom";
import {
  ConnectionSessionEditor,
  defaultConnectionSessionConfig,
  type ConnectionSessionConfig,
} from "../../agent";
import type { AgentConnectionOption } from "../../agent/transport";
import { PopperSelect } from "../../components/ui/PopperSelect";
import type {
  CreateDiscussionInput,
  DiscussionSessionConfiguration,
  ParticipantDraft,
} from "../domain";

interface NewDiscussionDialogProps {
  readonly connections: readonly AgentConnectionOption[];
  readonly sessionConfigurations: readonly DiscussionSessionConfiguration[];
  readonly loading: boolean;
  readonly creating: boolean;
  readonly initialWorkdir: string;
  readonly onChooseWorkdir: (current: string, apply: (path: string) => void) => void;
  readonly onCreate: (input: CreateDiscussionInput) => void;
  readonly onCancel: () => void;
}

interface ParticipantEditorState {
  readonly index: number | null;
  readonly name: string;
  readonly usesDefaultName: boolean;
  readonly session: ConnectionSessionConfig;
}

/** 新建阶段允许编辑草稿；提交后 participant 集合由后端冻结。 */
export function NewDiscussionDialog({
  connections,
  sessionConfigurations,
  loading,
  creating,
  initialWorkdir,
  onChooseWorkdir,
  onCreate,
  onCancel,
}: NewDiscussionDialogProps) {
  const [topic, setTopic] = useState("");
  const [workdir, setWorkdir] = useState(initialWorkdir);
  const [mode, setMode] = useState<"automatic" | "user_guided">("user_guided");
  const [maxRounds, setMaxRounds] = useState(3);
  const [participants, setParticipants] = useState<readonly ParticipantDraft[]>([]);
  const [editor, setEditor] = useState<ParticipantEditorState | null>(null);
  const [editorError, setEditorError] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);

  function openEditor(index: number | null): void {
    const existing = index === null ? null : participants[index];
    setEditorError(null);
    setEditor({
      index,
      name: existing?.name ?? `参与者 ${participants.length + 1}`,
      usesDefaultName: existing?.usesDefaultName ?? true,
      session: existing?.session ?? defaultConnectionSessionConfig(connections),
    });
  }

  function saveEditor(): void {
    if (!editor) return;
    const name = editor.name.trim();
    if (!name) {
      setEditorError("参与者名称不能为空");
      return;
    }
    if (
      participants.some(
        (item, index) => item.name === name && index !== editor.index,
      )
    ) {
      setEditorError("参与者名称不能重复");
      return;
    }
    if (!editor.session.connection_id || !editor.session.model) {
      setEditorError("请选择可用的连接和模型");
      return;
    }
    const participantNumber = editor.index === null
      ? participants.length + 1
      : editor.index + 1;
    const draft: ParticipantDraft = {
      localId:
        editor.index === null
          ? `participant-${crypto.randomUUID()}`
          : participants[editor.index].localId,
      name,
      usesDefaultName:
        editor.usesDefaultName || name === `参与者 ${participantNumber}`,
      session: editor.session,
    };
    setParticipants((current) =>
      editor.index === null
        ? [...current, draft]
        : current.map((item, index) => (index === editor.index ? draft : item)),
    );
    setEditorError(null);
    setEditor(null);
  }

  function submit(): void {
    if (!topic.trim()) {
      setFormError("需要填写讨论问题");
      return;
    }
    if (!workdir.trim()) {
      setFormError("需要选择工作区");
      return;
    }
    if (participants.length < 2 || participants.length > 8) {
      setFormError("研讨需要 2 至 8 位参与者");
      return;
    }
    if (participants.some((item) => !item.session.connection_id || !item.session.model)) {
      setFormError("有参与者尚未选择可用的连接和模型");
      return;
    }
    onCreate({
      request_id: `discussion:${crypto.randomUUID()}`,
      topic: topic.trim(),
      workdir,
      progression_mode: mode,
      max_rounds: mode === "automatic" ? maxRounds : null,
      participants: participants.map((item) => {
        const configuration = matchingConfiguration(item.session, sessionConfigurations);
        return {
          name: item.name,
          ...(configuration
            ? { session_configuration_id: configuration.id }
            : {
                connection_id: item.session.connection_id,
                model: item.session.model,
                effort: item.session.effort || null,
              }),
          memory_enabled: item.session.memory_enabled,
          profile_enabled: item.session.profile_enabled,
          self_enabled: item.session.self_enabled,
          permission_mode:
            item.session.runtime === "claude_code"
              ? item.session.permission_mode || "dontAsk"
              : null,
          permission_preset:
            item.session.runtime === "codex"
              ? (item.session.permission_preset ?? "read-only")
              : null,
        };
      }),
    });
  }

  if (editor) {
    const participantNumber = editor.index === null
      ? participants.length + 1
      : editor.index + 1;
    return createPortal(
      <div
        className="cc-dialog__backdrop"
        onMouseDown={() => {
          setEditorError(null);
          setEditor(null);
        }}
        role="presentation"
      >
        <section
          className="cc-dialog discussion-participant-editor"
          role="dialog"
          aria-modal="true"
          aria-label={editor.index === null ? "创建参与者" : `编辑参与者 ${participantNumber}`}
          onMouseDown={(event) => event.stopPropagation()}
          onKeyDown={(event) => {
            if (event.key === "Escape") {
              setEditorError(null);
              setEditor(null);
            }
          }}
        >
          <div className="cc-dialog__head discussion-participant-editor__head">
            <p className="cc-dialog__title">
              {editor.index === null ? "创建参与者" : `编辑参与者 ${participantNumber}`}
            </p>
            <span className="cc-dialog__workdir">
              参与者 {participantNumber} · 权限按参与者独立设置
            </span>
          </div>
          <div className="cc-dialog__body">
            <label className="discussion-participant-editor__name">
              <span className="cc-dialog__section-label">参与者名称</span>
              <input
                value={editor.name}
                maxLength={60}
                onChange={(event) => {
                  setEditorError(null);
                  setEditor({
                    ...editor,
                    name: event.target.value,
                    usesDefaultName: false,
                  });
                }}
              />
            </label>
            <ConnectionSessionEditor
              value={editor.session}
              connections={connections}
              disabled={creating}
              discussionParticipant
              onChange={(session) => {
                setEditorError(null);
                setEditor({ ...editor, session });
              }}
            />
            {editorError && (
              <p className="discussion-modal__error" role="alert">
                {editorError}
              </p>
            )}
          </div>
          <div className="cc-dialog__foot">
            <button
              type="button"
              className="cc-dialog__btn"
              onClick={() => {
                setEditorError(null);
                setEditor(null);
              }}
            >
              取消
            </button>
            <button
              type="button"
              className="cc-dialog__btn cc-dialog__btn--primary"
              disabled={creating || loading}
              onClick={saveEditor}
            >
              保存参与者
            </button>
          </div>
        </section>
      </div>,
      document.body,
    );
  }

  return createPortal(
    <div className="discussion-modal-backdrop" onMouseDown={onCancel}>
      <section
        className="discussion-modal discussion-new-modal"
        role="dialog"
        aria-modal="true"
        aria-label="新建研讨"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header>
          <h2>新建研讨</h2>
          <button type="button" onClick={onCancel} aria-label="关闭">
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="m6 6 12 12M18 6 6 18" />
            </svg>
          </button>
        </header>
        <div className="discussion-modal__body">
          <>
              <label className="discussion-field">
                <span>讨论问题</span>
                <textarea
                  value={topic}
                  onChange={(event) => {
                    setFormError(null);
                    setTopic(event.target.value);
                  }}
                  placeholder="需要多个模型独立判断什么问题？"
                />
              </label>
              <div className="discussion-field">
                <span>工作区</span>
                <button
                  type="button"
                  className="discussion-workspace-field"
                  onClick={() => onChooseWorkdir(workdir, setWorkdir)}
                >
                  <svg viewBox="0 0 24 24" aria-hidden="true">
                    <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z" />
                  </svg>
                  <span className="discussion-workspace-field__main">
                    <strong>{basename(workdir) || "选择工作区"}</strong>
                    <small>{workdir || "尚未选择"}</small>
                  </span>
                  <b>更换</b>
                </button>
              </div>
              <div className="discussion-participant-drafts__head">
                <div>
                  <strong>参与者</strong>
                  <span>{participantCountLabel(participants.length)}</span>
                </div>
                <button
                  type="button"
                  disabled={participants.length >= 8 || loading || connections.length === 0}
                  onClick={() => openEditor(null)}
                >
                  <svg viewBox="0 0 24 24" aria-hidden="true">
                    <path d="M12 5v14M5 12h14" />
                  </svg>
                  创建参与者
                </button>
              </div>
              <div className="discussion-participant-drafts">
                {participants.length === 0 && (
                  <div className="discussion-participant-empty" role="status">
                    <span aria-hidden="true">0 / 2</span>
                    <div>
                      <strong>先创建至少两位参与者</strong>
                      <p>每位参与者分别选择 Runtime、连接、模型和强度。</p>
                    </div>
                  </div>
                )}
                {participants.map((item, index) => {
                  const title = `参与者 ${index + 1}`;
                  const connection = connections.find(
                    (candidate) => candidate.id === item.session.connection_id,
                  );
                  const modelLabel = connection?.models.find(
                    (candidate) => candidate.id === item.session.model,
                  )?.display_name;
                  return (
                  <article key={item.localId} className="cc-multibar__item discussion-participant-draft">
                    <button
                      type="button"
                      className="cc-multibar__main"
                      onClick={() => openEditor(index)}
                      aria-label={`编辑 ${title}`}
                    >
                      <span className="cc-multibar__row1">
                        <span className="cc-multibar__dot cc-multibar__dot--idle" aria-hidden="true" />
                        <span className="cc-multibar__name">{title}</span>
                        <span className={`cc-runtime-badge cc-runtime-badge--${item.session.runtime}`}>
                          {item.session.runtime === "codex" ? "Codex" : "Claude"}
                        </span>
                      </span>
                      <span className="cc-multibar__row2">
                        {[item.name !== title ? item.name : null, connection?.name, modelLabel || item.session.model || "未选模型"]
                          .filter(Boolean)
                          .join(" · ")}
                      </span>
                      <span className="cc-multibar__cond" title="Memory · Profile · Self · 权限">
                        <span className={item.session.memory_enabled ? "cc-multibar__cond-on" : "cc-multibar__cond-off"}>M</span>
                        <span className="cc-multibar__cond-sep">·</span>
                        <span className={item.session.profile_enabled ? "cc-multibar__cond-on" : "cc-multibar__cond-off"}>P</span>
                        <span className="cc-multibar__cond-sep">·</span>
                        <span className={item.session.self_enabled ? "cc-multibar__cond-on" : "cc-multibar__cond-off"}>S</span>
                        <span className="cc-multibar__cond-sep">·</span>
                        <span className="cc-multibar__perm">{permissionLabel(item.session)}</span>
                      </span>
                    </button>
                    <button
                      type="button"
                      className="cc-multibar__action cc-multibar__action--close"
                      aria-label={`删除 ${title}`}
                      onClick={() => {
                        setFormError(null);
                        setParticipants((current) =>
                          renumberDefaultParticipants(
                            current.filter(
                              (candidate) => candidate.localId !== item.localId,
                            ),
                          ),
                        );
                      }}
                    >
                      <svg viewBox="0 0 24 24" aria-hidden="true">
                        <path d="m6 6 12 12M18 6 6 18" />
                      </svg>
                    </button>
                  </article>
                  );
                })}
              </div>
              <section className="discussion-progression">
                <div className="discussion-progression__label">推进方式</div>
                <div className="discussion-progression__choices" role="radiogroup" aria-label="推进方式">
                  <button
                    type="button"
                    role="radio"
                    aria-checked={mode === "user_guided"}
                    onClick={() => setMode("user_guided")}
                  >
                    <span className="discussion-progression__radio" aria-hidden="true" />
                    <span>
                      <strong>逐轮决定</strong>
                      <small>每轮共同公开后，由人决定是否继续</small>
                    </span>
                  </button>
                  <button
                    type="button"
                    role="radio"
                    aria-checked={mode === "automatic"}
                    onClick={() => setMode("automatic")}
                  >
                    <span className="discussion-progression__radio" aria-hidden="true" />
                    <span>
                      <strong>自动推进</strong>
                      <small>连续开始下一轮，到达上限后停止</small>
                    </span>
                  </button>
                </div>
                {mode === "automatic" && (
                  <div className="discussion-round-limit">
                    <span>
                      <strong>自动停止</strong>
                      <small>完成指定轮数后暂停，等待下一步</small>
                    </span>
                    <PopperSelect
                      ariaLabel="最多轮数"
                      value={String(maxRounds)}
                      options={[1, 2, 3, 4, 6, 8].map((value) => ({
                        value: String(value),
                        label: `最多 ${value} 轮`,
                      }))}
                      density="compact"
                      triggerClassName="discussion-round-limit__select"
                      onValueChange={(value) => setMaxRounds(Number(value))}
                    />
                  </div>
                )}
              </section>
            </>
          {formError && (
            <p className="discussion-modal__error" role="alert">
              {formError}
            </p>
          )}
        </div>
        <footer>
          <button type="button" onClick={onCancel}>
            取消
          </button>
          <button
            type="button"
            className="discussion-primary-button"
            disabled={creating || loading || participants.length < 2}
            onClick={submit}
          >
            {creating ? "正在创建…" : createButtonLabel(participants.length)}
          </button>
        </footer>
      </section>
    </div>,
    document.body,
  );
}

function participantCountLabel(count: number): string {
  if (count === 0) return "0 位 · 至少创建 2 位";
  if (count === 1) return "1 位 · 还需 1 位";
  return `${count} 位 · 同轮共同公开`;
}

function createButtonLabel(participantCount: number): string {
  if (participantCount === 0) return "还需创建 2 位参与者";
  if (participantCount === 1) return "还需创建 1 位参与者";
  return "创建并开始第 1 轮";
}

function matchingConfiguration(
  session: ConnectionSessionConfig,
  configurations: readonly DiscussionSessionConfiguration[],
): DiscussionSessionConfiguration | undefined {
  return configurations.find(
    (item) =>
      item.availability === "available" &&
      item.runtime === session.runtime &&
      item.connection_id === session.connection_id &&
      item.model === session.model &&
      (item.effort ?? "") === session.effort,
  );
}

/** 删除默认命名参与者后按当前槽位重编号，同时保留用户自定义名称。 */
function renumberDefaultParticipants(
  participants: readonly ParticipantDraft[],
): readonly ParticipantDraft[] {
  return participants.map((participant, index) =>
    participant.usesDefaultName
      ? { ...participant, name: `参与者 ${index + 1}` }
      : participant,
  );
}

function basename(path: string): string {
  return path.split("/").filter(Boolean).at(-1) ?? path;
}

/** 返回参与者创建时会冻结到对应 runtime 的权限名称。 */
function permissionLabel(session: ConnectionSessionConfig): string {
  return session.runtime === "codex"
    ? (session.permission_preset ?? "read-only")
    : session.permission_mode || "dontAsk";
}
