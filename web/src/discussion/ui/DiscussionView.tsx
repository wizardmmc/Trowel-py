/** 组合研讨主题、共同公开轮次、用户补充和状态允许的命令。 */

import { useMemo, useState } from "react";
import { PopperSelect } from "../../components/ui/PopperSelect";
import type { Discussion } from "../domain";
import { DiscussionInspector } from "./DiscussionInspector";
import { DiscussionRoundTimeline } from "./DiscussionRoundTimeline";

interface DiscussionViewProps {
  readonly discussion: Discussion;
  readonly pendingCommand: string | null;
  readonly error: string | null;
  readonly onStart: () => void;
  readonly onContinue: (
    progressionMode: "automatic" | "user_guided",
    additionalRounds?: number | null,
  ) => void;
  readonly onFinish: () => void;
  readonly onStop: () => void;
  readonly onResume: () => void;
  readonly onDelete: () => void;
  readonly onAddMessage: (body: string, participantId: string | null) => Promise<void>;
  readonly onMark: (roundNumber: number, participantId: string, marked: boolean) => void;
  readonly onHandoff: () => void;
  readonly onClearError: () => void;
}

/** 展示组件只发出用户意图，不订阅 store 或解释 transport 事件。 */
export function DiscussionView({
  discussion,
  pendingCommand,
  error,
  onStart,
  onContinue,
  onFinish,
  onStop,
  onResume,
  onDelete,
  onAddMessage,
  onMark,
  onHandoff,
  onClearError,
}: DiscussionViewProps) {
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const [message, setMessage] = useState("");
  const [targetId, setTargetId] = useState("");
  const [focusedParticipantId, setFocusedParticipantId] = useState<string | null>(null);
  const focusedId = discussion.participants.some(
    (participant) => participant.id === focusedParticipantId,
  )
    ? focusedParticipantId
    : null;
  const pending = pendingCommand !== null;
  const canHandoff = ["waiting_user", "completed", "stopped"].includes(
    discussion.status,
  );
  const supplements = useMemo(
    () => discussion.messages.filter((item) => item.sequence > 1),
    [discussion.messages],
  );

  async function submitMessage(): Promise<void> {
    const body = message.trim();
    if (!body || pending) return;
    try {
      await onAddMessage(body, targetId || null);
      setMessage("");
    } catch {
      // store 已保存可见错误；保留原文让用户重试。
    }
  }

  return (
    <div className="discussion-view">
      <section className="discussion-main">
        <header className="discussion-toolbar">
          <strong className="discussion-toolbar__title">{discussion.topic}</strong>
          <span className="discussion-status" data-status={discussion.status}>
            {statusLabel(discussion.status, discussion.active_round_number)}
          </span>
          <small className="discussion-toolbar__meta">
            权限按参与者冻结 · {discussion.participants.length} 个原生会话
          </small>
          <div className="discussion-toolbar__actions">
            <button
              type="button"
              className="discussion-toolbar__inspect"
              onClick={() => setInspectorOpen(true)}
              aria-label="查看参与者配置"
            >
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <rect x="3" y="4" width="18" height="16" rx="2" />
                <path d="M15 4v16" />
              </svg>
            </button>
            <button
              type="button"
              className="discussion-primary-button"
              disabled={!canHandoff || pending}
              onClick={onHandoff}
            >
              在 Agent 中继续
            </button>
          </div>
        </header>

        <div className="discussion-scroll">
          <section className="discussion-topic">
            <span className="discussion-topic__label">
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" />
                <circle cx="9" cy="7" r="4" />
              </svg>
              讨论问题
            </span>
            <h1>{discussion.topic}</h1>
            <p className="discussion-topic__meta">
              <span>{basename(discussion.workdir)}</span>
              <time dateTime={discussion.created_at}>{formatCreatedAt(discussion.created_at)}</time>
              <span>Memory {discussion.participants.some((item) => item.memory_enabled) ? "on" : "off"}</span>
              <span>Profile {discussion.participants.some((item) => item.profile_enabled) ? "on" : "off"}</span>
              <span>{discussion.progression_mode === "automatic" ? `最多 ${discussion.max_rounds} 轮` : "逐轮决定"}</span>
            </p>
          </section>

          {supplements.length > 0 && (
            <section className="discussion-supplements" aria-label="用户补充">
              {supplements.map((item) => (
                <article key={item.id}>
                  <span>第 {item.after_round_number} 轮后</span>
                  <strong>{messageTarget(item.target_participant_id, discussion)}</strong>
                  <p>{item.body}</p>
                </article>
              ))}
            </section>
          )}

          {discussion.rounds.length > 0 ? (
            <DiscussionRoundTimeline
              participants={discussion.participants}
              rounds={discussion.rounds}
              workdir={discussion.workdir}
              focusedParticipantId={focusedId}
              onFocusParticipant={setFocusedParticipantId}
              disabled={pending}
              onMark={onMark}
            />
          ) : (
            <section className="discussion-empty-round">
              <strong>参与者已经冻结，尚未开始回答</strong>
              <p>开始后，同一轮所有正文会一起公开。</p>
            </section>
          )}
        </div>

        {error && (
          <div className="discussion-error" role="alert">
            <span>{error}</span>
            <button type="button" onClick={onClearError}>关闭</button>
          </div>
        )}

        <DiscussionCommandBar
          discussion={discussion}
          pending={pending}
          message={message}
          targetId={targetId}
          onMessageChange={setMessage}
          onTargetChange={(value) => {
            setTargetId(value);
            setFocusedParticipantId(value || null);
          }}
          onSubmitMessage={() => void submitMessage()}
          onStart={onStart}
          onContinue={onContinue}
          onFinish={onFinish}
          onStop={onStop}
          onResume={onResume}
          onDelete={onDelete}
        />
      </section>

      <DiscussionInspector
        discussion={discussion}
        focusedParticipantId={focusedId}
        onFocusParticipant={(participantId) =>
          setFocusedParticipantId((current) =>
            current === participantId ? null : participantId,
          )
        }
        overlay={inspectorOpen}
        onClose={() => setInspectorOpen(false)}
      />
      {inspectorOpen && (
        <button
          type="button"
          className="discussion-inspector-backdrop"
          aria-label="关闭配置"
          onClick={() => setInspectorOpen(false)}
        />
      )}
    </div>
  );
}

function DiscussionCommandBar({
  discussion,
  pending,
  message,
  targetId,
  onMessageChange,
  onTargetChange,
  onSubmitMessage,
  onStart,
  onContinue,
  onFinish,
  onStop,
  onResume,
  onDelete,
}: {
  readonly discussion: Discussion;
  readonly pending: boolean;
  readonly message: string;
  readonly targetId: string;
  readonly onMessageChange: (value: string) => void;
  readonly onTargetChange: (value: string) => void;
  readonly onSubmitMessage: () => void;
  readonly onStart: () => void;
  readonly onContinue: (
    progressionMode: "automatic" | "user_guided",
    additionalRounds?: number | null,
  ) => void;
  readonly onFinish: () => void;
  readonly onStop: () => void;
  readonly onResume: () => void;
  readonly onDelete: () => void;
}) {
  const [automaticRounds, setAutomaticRounds] = useState(3);
  if (discussion.status === "draft") {
    return (
      <footer className="discussion-command-bar discussion-command-bar--simple">
        <span>开始后，第 1 轮参与者将并行回答。</span>
        <button type="button" className="discussion-primary-button" disabled={pending} onClick={onStart}>
          开始第 1 轮
        </button>
      </footer>
    );
  }
  if (discussion.status === "running") {
    return (
      <footer className="discussion-command-bar discussion-command-bar--simple">
        <span>本轮正在封闭回答，正文会在所有参与者进入终态后共同公开。</span>
        <button type="button" disabled={pending} onClick={onStop}>停止研讨</button>
      </footer>
    );
  }
  if (discussion.status === "needs_reconcile") {
    return (
      <footer className="discussion-command-bar discussion-command-bar--simple">
        <span>应用上次退出时仍有未对账的参与者资源。</span>
        <button type="button" className="discussion-primary-button" disabled={pending} onClick={onResume}>
          恢复并对账
        </button>
      </footer>
    );
  }
  if (discussion.status === "waiting_user") {
    return (
      <footer className="discussion-command-bar">
        <div className="discussion-composer">
          <div className="discussion-composer__targets" aria-label="补充对象">
            <span>发送给</span>
            <button
              type="button"
              aria-pressed={targetId === ""}
              disabled={pending}
              onClick={() => onTargetChange("")}
            >
              全体
            </button>
            {discussion.participants.map((participant) => (
              <button
                key={participant.id}
                type="button"
                aria-pressed={targetId === participant.id}
                disabled={pending}
                onClick={() => onTargetChange(participant.id)}
              >
                {participant.name}
              </button>
            ))}
          </div>
          <textarea
            value={message}
            disabled={pending}
            placeholder="可选：在下一轮前补充事实、约束或追问…"
            aria-label="研讨补充"
            onChange={(event) => onMessageChange(event.target.value)}
            onKeyDown={(event) => {
              if ((event.metaKey || event.ctrlKey) && event.key === "Enter") onSubmitMessage();
            }}
          />
          <button
            type="button"
            className="discussion-composer__send"
            disabled={pending || !message.trim()}
            onClick={onSubmitMessage}
          >
            发送补充
          </button>
        </div>
        <div className="discussion-command-actions">
          <span>下一轮会读取此前公开内容 · 当前 {discussion.active_round_number ?? 0} / {discussion.max_rounds ?? "—"} 轮</span>
          <button type="button" disabled={pending} onClick={onStop}>停止</button>
          <button type="button" disabled={pending} onClick={onFinish}>请求收尾</button>
          <button
            type="button"
            className="discussion-guided-button"
            disabled={pending}
            onClick={() => onContinue("user_guided", null)}
          >
            继续 1 轮
          </button>
          <div className="discussion-auto-continue">
            <PopperSelect
              ariaLabel="自动推进轮数"
              value={String(automaticRounds)}
              options={[1, 2, 3, 4, 6, 8].map((value) => ({
                value: String(value),
                label: `再 ${value} 轮`,
              }))}
              density="compact"
              side="top"
              triggerClassName="discussion-auto-continue__select"
              disabled={pending}
              onValueChange={(value) => setAutomaticRounds(Number(value))}
            />
            <button
              type="button"
              className="discussion-primary-button"
              disabled={pending}
              onClick={() => onContinue("automatic", automaticRounds)}
            >
              自动推进
            </button>
          </div>
        </div>
      </footer>
    );
  }
  return (
    <footer className="discussion-command-bar discussion-command-bar--simple">
      <span>{discussion.status === "completed" ? "研讨已完成，可交给普通 Agent 继续执行。" : "研讨已停止，公开结果和完整记录仍然保留。"}</span>
      <button type="button" disabled={pending} onClick={onDelete}>删除研讨</button>
    </footer>
  );
}

function statusLabel(
  status: Discussion["status"],
  roundNumber: number | null,
): string {
  const labels = {
    draft: "尚未开始",
    running: roundNumber ? `第 ${roundNumber} 轮进行中` : "回答中",
    waiting_user: roundNumber ? `第 ${roundNumber} 轮已公开` : "等待决定",
    completed: "已收尾",
    stopped: "已停止",
    needs_reconcile: "等待恢复",
  };
  return labels[status];
}

function basename(path: string): string {
  return path.split(/[\\/]/).filter(Boolean).at(-1) ?? path;
}

function formatCreatedAt(value: string): string {
  const createdAt = new Date(value);
  if (!Number.isFinite(createdAt.getTime())) return "";
  const year = createdAt.getFullYear();
  const month = String(createdAt.getMonth() + 1).padStart(2, "0");
  const day = String(createdAt.getDate()).padStart(2, "0");
  const hour = String(createdAt.getHours()).padStart(2, "0");
  const minute = String(createdAt.getMinutes()).padStart(2, "0");
  return `${year}-${month}-${day} ${hour}:${minute}`;
}

function messageTarget(participantId: string | null, discussion: Discussion): string {
  if (!participantId) return "给全体";
  return `只给 ${discussion.participants.find((item) => item.id === participantId)?.name ?? "指定参与者"}`;
}
