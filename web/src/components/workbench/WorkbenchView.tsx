import { useEffect, useMemo, useState } from "react";
import type { FormEvent } from "react";

import type {
  WorkbenchCandidate,
  WorkbenchRecentEvent,
  WorkbenchTask,
} from "../../api/modelOs";
import { useWorkbenchStore } from "../../stores/workbenchStore";
import "./WorkbenchView.css";

type Section = "tasks" | "now" | "inbox";

const statusLabels: Record<string, string> = {
  ready: "待执行",
  running: "进行中",
  waiting_user: "等人处理",
  waiting_event: "等外部条件",
  paused: "已暂停",
};

const eventLabels: Record<string, string> = {
  "attention.schedule": "重新计算任务顺序",
  "automation.mode_changed": "自动调度模式已改变",
  "task.priority_changed": "任务优先级已改变",
  "task.promoted_to_warm": "任务已加入近期队列",
  "task.demoted_to_backlog": "任务已移回长期列表",
};

function runtimeLabel(runtime: string | null): string {
  if (runtime === "codex") return "Codex";
  if (runtime === "claude_code") return "Claude Code";
  return "尚未分配";
}

function taskStateLabel(task: WorkbenchTask): string {
  return statusLabels[task.status] ?? task.status;
}

function timeLabel(timestamp: string): string {
  const value = Date.parse(timestamp);
  if (!Number.isFinite(value)) return timestamp;
  const delta = Date.now() - value;
  if (delta < 60_000) return "刚刚";
  if (delta < 3_600_000) return `${Math.max(1, Math.floor(delta / 60_000))} 分钟前`;
  if (delta < 86_400_000) return `${Math.floor(delta / 3_600_000)} 小时前`;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(value);
}

function currentReason(task: WorkbenchTask | undefined): string {
  if (!task) return "当前没有正在执行的任务。";
  if (task.current_judgment) return task.current_judgment;
  if (task.running) return "对应的 Agent 会话仍在运行。";
  return "这是当前获得前台执行权的任务。";
}

function nextAction(task: WorkbenchTask | undefined): string {
  if (!task) return "等待下一次调度";
  if (task.next_steps.length > 0) return task.next_steps[0];
  if (task.waiting?.cause) return task.waiting.cause;
  return "继续完成当前任务";
}

function TaskControls({ task }: { readonly task: WorkbenchTask }) {
  const actionPending = useWorkbenchStore((store) => store.actionPending);
  const toggleWarm = useWorkbenchStore((store) => store.toggleWarm);
  const setPriority = useWorkbenchStore((store) => store.setPriority);
  const requestForeground = useWorkbenchStore(
    (store) => store.requestForeground,
  );
  const pending = actionPending?.endsWith(task.task_id) ?? false;

  return (
    <div className="workbench-task__actions">
      {!task.is_foreground && (
        <button
          className="workbench-mini-button"
          type="button"
          disabled={pending}
          onClick={() => void requestForeground(task.task_id)}
        >
          现在处理
        </button>
      )}
      {!task.is_foreground && (
        <button
          className="workbench-mini-button"
          type="button"
          disabled={pending}
          onClick={() => void toggleWarm(task.task_id, !task.warm)}
        >
          {task.warm ? "移到以后" : "放到近期"}
        </button>
      )}
      <label className="workbench-priority">
        优先级
        <select
          value={task.priority}
          disabled={pending}
          onChange={(event) =>
            void setPriority(task.task_id, Number(event.target.value))
          }
        >
          {[9, 8, 7, 6, 5, 4, 3, 2, 1, 0].map((value) => (
            <option key={value} value={value}>
              {value}
            </option>
          ))}
        </select>
      </label>
    </div>
  );
}

function TaskRow({
  task,
  rank,
  isNext = false,
}: {
  readonly task: WorkbenchTask;
  readonly rank?: number;
  readonly isNext?: boolean;
}) {
  return (
    <article
      className={`workbench-task${task.is_foreground ? " workbench-task--current" : ""}`}
    >
      <div className="workbench-task__top">
        {rank !== undefined && <span className="workbench-rank">{rank}</span>}
        <div className="workbench-task__copy">
          <div className="workbench-task__name">{task.goal}</div>
          <div className="workbench-task__meta">
            <span>{taskStateLabel(task)}</span>
            {isNext && <span className="workbench-tag workbench-tag--gold">下一项</span>}
            {task.waiting && (
              <span className="workbench-tag workbench-tag--red">需要处理</span>
            )}
          </div>
        </div>
      </div>
      {task.is_foreground && (
        <p className="workbench-task__reason">{currentReason(task)}</p>
      )}
      <TaskControls task={task} />
    </article>
  );
}

function TasksColumn({
  tasks,
  nextTaskId,
}: {
  readonly tasks: readonly WorkbenchTask[];
  readonly nextTaskId: string | null;
}) {
  const current = tasks.find((task) => task.is_foreground);
  const warm = tasks.filter((task) => task.warm && !task.is_foreground);
  const backlog = tasks.filter((task) => !task.warm && !task.is_foreground);

  return (
    <section className="workbench-column" data-section="tasks">
      <div className="workbench-surface workbench-surface--fill">
        <header className="workbench-surface__head">
          <h2>任务</h2>
          <span className="workbench-count">{tasks.length}</span>
          <span className="workbench-surface__note">顺序来自调度器</span>
        </header>
        <div className="workbench-section">
          <div className="workbench-section__kicker">当前任务</div>
          {current ? (
            <TaskRow task={current} />
          ) : (
            <p className="workbench-empty">当前没有前台任务。</p>
          )}
        </div>
        <div className="workbench-section">
          <div className="workbench-section__kicker">
            近期队列
            <span>{warm.length}</span>
          </div>
          {warm.length > 0 ? (
            warm.map((task, index) => (
              <TaskRow
                key={task.task_id}
                task={task}
                rank={index + 1}
                isNext={task.task_id === nextTaskId}
              />
            ))
          ) : (
            <p className="workbench-empty">近期没有排队任务。</p>
          )}
        </div>
        <div className="workbench-section workbench-section--grow">
          <div className="workbench-section__kicker">
            以后再做
            <span>{backlog.length}</span>
          </div>
          {backlog.length > 0 ? (
            backlog.map((task) => <TaskRow key={task.task_id} task={task} />)
          ) : (
            <p className="workbench-empty">没有长期待办。</p>
          )}
        </div>
      </div>
    </section>
  );
}

function ActivityRow({ item }: { readonly item: WorkbenchRecentEvent }) {
  return (
    <li className="workbench-activity__item">
      <span
        className={`workbench-activity__dot workbench-activity__dot--${item.stream}`}
      />
      <div>
        <div className="workbench-activity__title">
          {eventLabels[item.kind] ?? item.kind}
        </div>
        <div className="workbench-activity__meta">
          {item.reason ?? item.outcome ?? "已记录"}
          <span>{timeLabel(item.recorded_at)}</span>
        </div>
      </div>
    </li>
  );
}

function NowColumn({
  current,
  recentEvents,
}: {
  readonly current: WorkbenchTask | undefined;
  readonly recentEvents: readonly WorkbenchRecentEvent[];
}) {
  return (
    <section className="workbench-column" data-section="now">
      <article className="workbench-surface workbench-episode">
        <div className="workbench-episode__state">
          {current ? taskStateLabel(current) : "空闲"}
        </div>
        <h2>{current?.goal ?? "当前没有执行中的任务"}</h2>
        <p className="workbench-episode__lead">{currentReason(current)}</p>
        <div className="workbench-episode__facts">
          <span>
            执行者
            <strong>
              {current
                ? `${runtimeLabel(current.runtime)}${current.model ? ` · ${current.model}` : ""}`
                : "尚未分配"}
            </strong>
          </span>
          <span>
            连接
            <strong>
              {current?.connected === true
                ? "在线"
                : current?.connected === false
                  ? "已断开"
                  : "未连接"}
            </strong>
          </span>
          {current?.effort && (
            <span>
              思考强度<strong>{current.effort}</strong>
            </span>
          )}
        </div>
        <div className="workbench-next">
          <div className="workbench-next__label">接下来</div>
          <div className="workbench-next__value">{nextAction(current)}</div>
          {current && current.next_steps.length > 1 && (
            <div className="workbench-next__meta">
              随后：{current.next_steps.slice(1).join("；")}
            </div>
          )}
        </div>
      </article>
      <article className="workbench-surface workbench-surface--grow">
        <header className="workbench-surface__head">
          <h2>最近发生</h2>
          <span className="workbench-surface__note">系统原始记录的简明表述</span>
        </header>
        {recentEvents.length > 0 ? (
          <ol className="workbench-activity">
            {recentEvents.map((item) => (
              <ActivityRow key={`${item.stream}-${item.entry_id}`} item={item} />
            ))}
          </ol>
        ) : (
          <p className="workbench-empty workbench-empty--padded">
            暂时没有新的系统记录。
          </p>
        )}
      </article>
    </section>
  );
}

function WaitingItem({ task }: { readonly task: WorkbenchTask }) {
  const replyWaiting = useWorkbenchStore((store) => store.replyWaiting);
  const actionPending = useWorkbenchStore((store) => store.actionPending);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const waiting = task.waiting;
  if (!waiting) return null;
  const pending = task.pending_request;
  const questions = pending?.kind === "input" ? pending.questions : [];
  const decisions =
    pending?.kind === "approval"
      ? pending.available_decisions.filter(
          (decision): decision is string => typeof decision === "string",
        )
      : [];
  const allAnswered =
    questions.length > 0 &&
    questions.every((question) => answers[question.question]?.trim());

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!pending || pending.kind !== "input" || !allAnswered) return;
    await replyWaiting(task.task_id, pending.request_id, {
      answers: Object.fromEntries(
        questions.map((question) => [
          question.question,
          answers[question.question].trim(),
        ]),
      ),
    });
    if (!useWorkbenchStore.getState().error) setAnswers({});
  };

  return (
    <article className="workbench-inbox-item workbench-inbox-item--waiting">
      <div className="workbench-inbox-item__eyebrow">等待回复</div>
      <h3>{task.goal}</h3>
      <p>{waiting.cause}</p>
      {pending?.kind === "input" ? (
        <form className="workbench-reply" onSubmit={submit}>
          {questions.map((question, index) => (
            <div className="workbench-reply__question" key={question.question}>
              <label htmlFor={`reply-${task.task_id}-${index}`}>
                {question.question}
              </label>
              <textarea
                id={`reply-${task.task_id}-${index}`}
                aria-label={
                  index === 0
                    ? `回复：${task.goal}`
                    : `回复：${task.goal}（${index + 1}）`
                }
                value={answers[question.question] ?? ""}
                rows={2}
                placeholder="直接输入决定或补充信息"
                onChange={(event) =>
                  setAnswers({
                    ...answers,
                    [question.question]: event.target.value,
                  })
                }
              />
            </div>
          ))}
          <div className="workbench-reply__actions">
            <span>回复将绑定原请求，再唤醒对应任务</span>
            <button
              type="submit"
              aria-label={`回复“${task.goal}”`}
              disabled={
                !allAnswered || actionPending === `reply:${task.task_id}`
              }
            >
              发送回复
            </button>
          </div>
        </form>
      ) : pending?.kind === "approval" && decisions.length > 0 ? (
        <div className="workbench-approval-actions">
          {decisions.map((decision) => (
            <button
              key={decision}
              type="button"
              disabled={actionPending === `reply:${task.task_id}`}
              onClick={() =>
                void replyWaiting(task.task_id, pending.request_id, {
                  decision,
                })
              }
            >
              {decision}
            </button>
          ))}
        </div>
      ) : (
        <div className="workbench-inline-notice">
          原请求当前不可确认，请到 Agent 页面查看详细请求。
        </div>
      )}
    </article>
  );
}

function CandidateItem({ candidate }: { readonly candidate: WorkbenchCandidate }) {
  const recordOutcome = useWorkbenchStore(
    (store) => store.recordCandidateOutcome,
  );
  const actionPending = useWorkbenchStore((store) => store.actionPending);
  const [markingInvalid, setMarkingInvalid] = useState(false);
  const [invalidReason, setInvalidReason] = useState("");
  const pending = actionPending === `candidate:${candidate.candidate_id}`;

  const submitInvalid = async (event: FormEvent) => {
    event.preventDefault();
    const reason = invalidReason.trim();
    if (!reason) return;
    await recordOutcome(
      candidate.source_kind,
      candidate.candidate_id,
      "invalid",
      reason,
    );
    if (!useWorkbenchStore.getState().error) {
      setInvalidReason("");
      setMarkingInvalid(false);
    }
  };

  return (
    <article className="workbench-inbox-item">
      <div className="workbench-inbox-item__eyebrow">
        {candidate.source_kind === "incubation" ? "孵化结果" : "空闲时发现"}
      </div>
      <h3>{candidate.title}</h3>
      {candidate.why_useful && <p>{candidate.why_useful}</p>}
      {!candidate.why_useful && candidate.related_question && (
        <p>关联问题：{candidate.related_question}</p>
      )}
      <div className="workbench-candidate__proof">
        {candidate.verification || "尚未填写验证方式"}
      </div>
      <div className="workbench-candidate__actions">
        <button
          type="button"
          aria-label="采纳候选"
          disabled={pending}
          onClick={() =>
            void recordOutcome(
              candidate.source_kind,
              candidate.candidate_id,
              "adopted",
            )
          }
        >
          采纳
        </button>
        <button
          type="button"
          disabled={pending}
          onClick={() =>
            void recordOutcome(
              candidate.source_kind,
              candidate.candidate_id,
              "dismissed",
            )
          }
        >
          暂不采用
        </button>
        <button
          type="button"
          disabled={pending}
          onClick={() => setMarkingInvalid((value) => !value)}
        >
          标记有误
        </button>
      </div>
      {markingInvalid && (
        <form className="workbench-invalid" onSubmit={submitInvalid}>
          <label htmlFor={`candidate-invalid-${candidate.candidate_id}`}>
            错误原因
          </label>
          <div>
            <input
              id={`candidate-invalid-${candidate.candidate_id}`}
              value={invalidReason}
              placeholder="说明哪里不准确"
              onChange={(event) => setInvalidReason(event.target.value)}
            />
            <button type="submit" disabled={pending || !invalidReason.trim()}>
              确认
            </button>
          </div>
        </form>
      )}
    </article>
  );
}

function InboxColumn({
  waitingTasks,
  candidates,
}: {
  readonly waitingTasks: readonly WorkbenchTask[];
  readonly candidates: readonly WorkbenchCandidate[];
}) {
  return (
    <section className="workbench-column" data-section="inbox">
      <div className="workbench-surface workbench-surface--fill">
        <header className="workbench-surface__head">
          <h2>待我处理</h2>
          <span className="workbench-count">
            {waitingTasks.length + candidates.length}
          </span>
          <span className="workbench-surface__note">需要明确决定的事项</span>
        </header>
        <div className="workbench-inbox-list">
          {waitingTasks.map((task) => (
            <WaitingItem key={task.task_id} task={task} />
          ))}
          {candidates.map((candidate) => (
            <CandidateItem key={candidate.candidate_id} candidate={candidate} />
          ))}
          {waitingTasks.length === 0 && candidates.length === 0 && (
            <div className="workbench-inbox-empty">
              <div className="workbench-inbox-empty__mark">✓</div>
              <strong>没有需要处理的事项</strong>
              <span>新的等待和候选结果会出现在这里。</span>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}

export function WorkbenchView() {
  const {
    snapshot,
    loading,
    actionPending,
    error,
    load,
    connect,
    setAutomation,
    sendInstruction,
    clearError,
  } = useWorkbenchStore();
  const [instruction, setInstruction] = useState("");
  const [section, setSection] = useState<Section>("now");

  useEffect(() => {
    void load();
    return connect();
  }, [connect, load]);

  const current = snapshot?.tasks.find((task) => task.is_foreground);
  const next = snapshot?.tasks.find(
    (task) => task.task_id === snapshot.next_task_id,
  );
  const waitingTasks = useMemo(
    () => snapshot?.tasks.filter((task) => task.waiting !== null) ?? [],
    [snapshot],
  );

  const submitInstruction = async (event: FormEvent) => {
    event.preventDefault();
    const text = instruction.trim();
    if (!text || !current?.agent_session_id || !current.can_send_message) return;
    await sendInstruction(current.task_id, text);
    if (!useWorkbenchStore.getState().error) setInstruction("");
  };

  if (loading && !snapshot) {
    return (
      <main className="workbench-page workbench-loading">
        <span className="workbench-loading__dot" />
        正在读取工作台
      </main>
    );
  }

  if (!snapshot) {
    return (
      <main className="workbench-page workbench-unavailable">
        <h1>工作台暂不可用</h1>
        <p>{error ?? "没有取得 Model OS 的当前状态。"}</p>
        <button type="button" onClick={() => void load()}>
          重新读取
        </button>
      </main>
    );
  }

  const canSend = Boolean(current?.agent_session_id && current.can_send_message);

  return (
    <main className="workbench-page">
      <header className="workbench-head">
        <div className="workbench-head__title">
          <h1>工作台</h1>
          <p>任务进展、执行现场和需要处理的事项</p>
        </div>
        <form className="workbench-composer" onSubmit={submitInstruction}>
          <div className="workbench-composer__field">
            <label htmlFor="workbench-instruction">给 Trowel 新指令</label>
            <textarea
              id="workbench-instruction"
              rows={1}
              value={instruction}
              placeholder={
                canSend
                  ? "补充要求、纠正方向，或安排接下来的工作"
                  : "当前任务没有可连接的 Agent 会话"
              }
              disabled={!canSend}
              onChange={(event) => setInstruction(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  event.currentTarget.form?.requestSubmit();
                }
              }}
            />
          </div>
          <button
            type="submit"
            aria-label="发送新指令"
            disabled={
              !canSend ||
              !instruction.trim() ||
              actionPending === `instruction:${current?.task_id}`
            }
          >
            发送
          </button>
        </form>
        <div className="workbench-head__actions">
          <div
            className={`workbench-mode${snapshot.automation_paused ? " workbench-mode--paused" : ""}`}
          >
            {snapshot.automation_paused ? "在线 · 手动模式" : "在线 · 自动调度中"}
          </div>
          <button
            className="workbench-mode-button"
            type="button"
            aria-label={
              snapshot.automation_paused ? "恢复自动调度" : "进入手动模式"
            }
            disabled={actionPending === "automation"}
            onClick={() => void setAutomation(!snapshot.automation_paused)}
          >
            {snapshot.automation_paused ? "恢复自动调度" : "进入手动模式"}
          </button>
        </div>
      </header>

      {error && (
        <div className="workbench-error" role="alert">
          <span>{error}</span>
          <button type="button" onClick={clearError}>
            关闭
          </button>
        </div>
      )}

      <section className="workbench-facts" aria-label="当前情况">
        <div>
          <span>当前</span>
          <strong>{current ? "1 个任务正在执行" : "没有执行中的任务"}</strong>
          <small>{current ? taskStateLabel(current) : "系统处于空闲状态"}</small>
        </div>
        <div>
          <span>下一项</span>
          <strong>{next ? "近期队列第 1 项" : "尚未安排"}</strong>
          <small>{next ? next.goal : "等待新的任务或调度结果"}</small>
        </div>
        <div>
          <span>执行会话</span>
          <strong className="workbench-facts__accent">
            {current ? runtimeLabel(current.runtime) : "尚未分配"}
          </strong>
          <small>
            {current?.connected ? "连接正常" : "当前没有在线执行会话"}
          </small>
        </div>
        <div>
          <span>需要处理</span>
          <strong>{waitingTasks.length + snapshot.candidates.length} 项</strong>
          <small>
            {waitingTasks.length > 0
              ? `${waitingTasks.length} 项正在等待回复`
              : "没有任务等待回复"}
          </small>
        </div>
      </section>

      <nav className="workbench-tabs" aria-label="工作台分区">
        {(
          [
            ["tasks", "任务"],
            ["now", "执行现场"],
            ["inbox", "待我处理"],
          ] as const
        ).map(([id, label]) => (
          <button
            key={id}
            type="button"
            className={section === id ? "is-active" : ""}
            onClick={() => setSection(id)}
          >
            {label}
          </button>
        ))}
      </nav>

      <div className={`workbench-grid workbench-grid--mobile-${section}`}>
        <TasksColumn
          tasks={snapshot.tasks}
          nextTaskId={snapshot.next_task_id}
        />
        <NowColumn current={current} recentEvents={snapshot.recent_events} />
        <InboxColumn
          waitingTasks={waitingTasks}
          candidates={snapshot.candidates}
        />
      </div>
    </main>
  );
}
