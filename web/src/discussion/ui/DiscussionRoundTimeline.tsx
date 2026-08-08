/** 按共同公开轮次展示稳定参与者顺序、进度和真实失败原因。 */

import { useLayoutEffect, useMemo, useRef, useState } from "react";
import { AssistantText } from "../../components/cc/AssistantText";
import { EventTimeline } from "../../components/cc/EventTimeline";
import { ThinkingSpinner } from "../../components/cc/SpinnerLine";
import { useStickyBottom } from "../../components/cc/useStickyBottom";
import { getExpectedRuntimePresentation } from "../../agent/runtimes";
import type {
  DiscussionAttemptTimeline,
  DiscussionParticipant,
  DiscussionRound,
  DiscussionRoundParticipant,
} from "../domain";
import {
  attemptDurationSeconds,
  attemptTerminalStatus,
  attemptTimelineItems,
  splitCompletedItems,
} from "../domain";
import { balancedRows } from "./layout";

const TERMINAL_RESULT_STATUSES = new Set([
  "succeeded",
  "failed",
  "limited",
  "timed_out",
  "interrupted",
  "cancelled",
  "host_lost",
]);

interface DiscussionRoundTimelineProps {
  readonly participants: readonly DiscussionParticipant[];
  readonly rounds: readonly DiscussionRound[];
  readonly workdir?: string;
  readonly focusedParticipantId?: string | null;
  readonly onFocusParticipant?: (participantId: string) => void;
  readonly disabled: boolean;
  readonly attemptTimelines?: Readonly<Record<string, DiscussionAttemptTimeline>>;
  readonly onAnswerQuestion?: (
    participantId: string,
    attemptId: string,
    requestId: string,
    answers: Readonly<Record<string, string>>,
  ) => void;
  readonly onMark: (
    roundNumber: number,
    participantId: string,
    marked: boolean,
  ) => void;
}

/** 按稳定顺序展示参与者；窄屏改为单列但不隐藏任何人的实时进度。 */
export function DiscussionRoundTimeline({
  participants,
  rounds,
  workdir,
  focusedParticipantId = null,
  onFocusParticipant,
  disabled,
  attemptTimelines = {},
  onAnswerQuestion,
  onMark,
}: DiscussionRoundTimelineProps) {
  const [selectedId, setSelectedId] = useState(participants[0]?.id ?? "");
  const focused = participants.some((item) => item.id === focusedParticipantId)
    ? focusedParticipantId
    : null;
  const localSelected = participants.some((item) => item.id === selectedId)
    ? selectedId
    : (participants[0]?.id ?? "");
  const selected = focused ?? localSelected;

  return (
    <>
      <nav className="discussion-participant-tabs" aria-label="逐个查看参与者">
        <span>查看</span>
        {participants.map((item) => (
          <button
            key={item.id}
            type="button"
            aria-pressed={selected === item.id}
            onClick={() => {
              setSelectedId(item.id);
              onFocusParticipant?.(item.id);
            }}
          >
            {item.name}
          </button>
        ))}
      </nav>
      {rounds.map((round) => (
        <RoundSection
          key={round.id}
          round={round}
          participants={participants}
          selectedParticipantId={selected}
          focusedParticipantId={focused}
          totalDiscussionParticipants={participants.length}
          workdir={workdir}
          disabled={disabled}
          attemptTimelines={attemptTimelines}
          onAnswerQuestion={onAnswerQuestion}
          onMark={onMark}
        />
      ))}
    </>
  );
}

function RoundSection({
  round,
  participants,
  selectedParticipantId,
  focusedParticipantId,
  totalDiscussionParticipants,
  workdir,
  disabled,
  attemptTimelines,
  onAnswerQuestion,
  onMark,
}: {
  readonly round: DiscussionRound;
  readonly participants: readonly DiscussionParticipant[];
  readonly selectedParticipantId: string;
  readonly focusedParticipantId: string | null;
  readonly totalDiscussionParticipants: number;
  readonly workdir?: string;
  readonly disabled: boolean;
  readonly attemptTimelines: Readonly<Record<string, DiscussionAttemptTimeline>>;
  readonly onAnswerQuestion?: DiscussionRoundTimelineProps["onAnswerQuestion"];
  readonly onMark: DiscussionRoundTimelineProps["onMark"];
}) {
  const participantById = useMemo(
    () => new Map(participants.map((item) => [item.id, item] as const)),
    [participants],
  );
  const rows = useMemo(
    () => balancedRows(round.participants),
    [round.participants],
  );
  const published = round.status === "published";
  return (
    <section className="discussion-round" aria-label={`第 ${round.number} 轮`}>
      <header className="discussion-round__head">
        <span>{round.number}</span>
        <div>
          <strong>{round.kind === "final" ? "收尾陈述" : "独立判断"}</strong>
          <small>{published ? "参与者在本轮完成前互相不可见" : "回答完成前保持封闭"}</small>
        </div>
        <b data-published={published}>
          {published
            ? `${round.terminal_participants}/${round.total_participants} 同时公开`
            : `${round.terminal_participants}/${round.total_participants} 等待共同公开`}
        </b>
      </header>
      <div className="discussion-round__rows">
        {rows.map((row, rowIndex) => (
          <div
            key={`${round.id}-row-${rowIndex}`}
            className={`discussion-card-row discussion-card-row--${row.length}${row.length === 2 && ![2, 4].includes(totalDiscussionParticipants) ? " discussion-card-row--narrow-pair" : ""}`}
          >
            {row.map((item) => (
              <ResponseCard
                key={item.participant_id}
                item={item}
                participant={participantById.get(item.participant_id)}
                roundNumber={round.number}
                published={published}
                workdir={workdir}
                selected={item.participant_id === selectedParticipantId}
                dimmed={
                  focusedParticipantId !== null &&
                  item.participant_id !== focusedParticipantId
                }
                disabled={disabled}
                timeline={
                  item.current_attempt_id
                    ? attemptTimelines[item.current_attempt_id]
                    : undefined
                }
                onAnswerQuestion={onAnswerQuestion}
                onMark={onMark}
              />
            ))}
          </div>
        ))}
      </div>
    </section>
  );
}

function ResponseCard({
  item,
  participant,
  roundNumber,
  published,
  workdir,
  selected,
  dimmed,
  disabled,
  timeline,
  onAnswerQuestion,
  onMark,
}: {
  readonly item: DiscussionRoundParticipant;
  readonly participant?: DiscussionParticipant;
  readonly roundNumber: number;
  readonly published: boolean;
  readonly workdir?: string;
  readonly selected: boolean;
  readonly dimmed: boolean;
  readonly disabled: boolean;
  readonly timeline?: DiscussionAttemptTimeline;
  readonly onAnswerQuestion?: DiscussionRoundTimelineProps["onAnswerQuestion"];
  readonly onMark: DiscussionRoundTimelineProps["onMark"];
}) {
  const observedTerminal = attemptTerminalStatus(timeline);
  const effectiveStatus =
    item.status === "sealed" && observedTerminal ? observedTerminal : item.status;
  const succeeded = effectiveStatus === "succeeded";
  const failed = isTerminalResultStatus(effectiveStatus) && !succeeded;
  const items = attemptTimelineItems(timeline);
  const split = splitCompletedItems(items);
  const pendingQuestion = [...items]
    .reverse()
    .find(
      (entry) =>
        entry.kind === "elicit" &&
        entry.status === "pending" &&
        entry.toolName === "AskUserQuestion",
    );
  const presentation = timeline
    ? getExpectedRuntimePresentation(timeline.runtime)
    : undefined;
  const finalText = succeeded
    ? (published ? item.content : split.trailingText) ?? ""
    : "";
  const hasLiveContent = items.length > 0;
  const thinkingStartedAt = liveThinkingStartedAt(
    item,
    participant,
    timeline,
    effectiveStatus,
    hasLiveContent,
  );
  const showThinkingSpinner = thinkingStartedAt !== null;
  const bodyRef = useRef<HTMLDivElement>(null);
  const followKey = timeline?.attemptId ?? `${roundNumber}:${item.participant_id}`;
  const { stickyRef } = useStickyBottom(bodyRef, items.length, followKey);
  useLayoutEffect(() => {
    const body = bodyRef.current;
    if (!body || !stickyRef.current || typeof body.scrollTo !== "function") {
      return;
    }
    const frame = window.requestAnimationFrame(() => {
      if (!stickyRef.current) return;
      body.scrollTo({ top: body.scrollHeight, behavior: "auto" });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [effectiveStatus, showThinkingSpinner, stickyRef, timeline]);
  return (
    <article
      className={`discussion-response${selected ? " discussion-response--selected" : ""}${dimmed ? " discussion-response--dimmed" : ""}${failed ? " discussion-response--failed" : ""}`}
      data-participant-id={item.participant_id}
      data-participant-position={item.position % 5}
      data-status={effectiveStatus}
    >
      <header>
        <span>{item.name.slice(0, 1)}</span>
        <div className="discussion-response__identity">
          <strong>{item.name}</strong>
          {participant && (
            <small>{participantSubtitle(participant)}</small>
          )}
        </div>
        <b>{statusLabel(effectiveStatus)}</b>
      </header>
      <div ref={bodyRef} className="discussion-response__body">
        {succeeded && split.workItems.length > 0 && (
          <WorkedFold
            items={split.workItems}
            duration={formatAttemptDuration(item, timeline)}
            workdir={workdir}
            presentation={presentation}
          />
        )}
        {succeeded && finalText ? (
          <div className="discussion-response__answer">
            <AssistantText text={finalText} workdir={workdir} />
          </div>
        ) : failed ? (
          <>
            {hasLiveContent && (
              <EventTimeline
                items={items}
                isReplay
                workdir={workdir}
                presentation={presentation}
              />
            )}
            <p className="discussion-response__error">
              {item.error_message ?? item.error_code ?? statusLabel(effectiveStatus)}
            </p>
          </>
        ) : hasLiveContent || showThinkingSpinner ? (
          <>
            {hasLiveContent && (
              <EventTimeline
                items={items}
                workdir={workdir}
                presentation={presentation}
                sessionId={timeline?.attemptId}
                onAnswer={
                  pendingQuestion?.kind === "elicit" &&
                  item.current_attempt_id &&
                  onAnswerQuestion
                    ? (answers) =>
                        onAnswerQuestion(
                          item.participant_id,
                          item.current_attempt_id as string,
                          pendingQuestion.requestId,
                          answers,
                        )
                    : undefined
                }
              />
            )}
            {showThinkingSpinner && (
              <ThinkingSpinner
                thinkingStartedAt={thinkingStartedAt}
                thinkingTokens={timeline?.reducer.meta.thinkingTokens ?? null}
                stallWarning={timeline?.reducer.meta.stallWarning ?? null}
                effort={participant?.effort ?? null}
              />
            )}
          </>
        ) : (
          <p className="discussion-response__sealed">
            {item.status === "pending" ? "等待该参与者开始" : "正在建立实时轨迹…"}
          </p>
        )}
      </div>
      {published && (
        <footer>
          <span title={activityTitle(item.activity)}>
            {footerFacts(item)}
          </span>
          <button
            type="button"
            disabled={disabled}
            aria-pressed={item.marked}
            onClick={() => onMark(roundNumber, item.participant_id, !item.marked)}
          >
            {item.marked ? "已标记" : "标记"}
          </button>
        </footer>
      )}
    </article>
  );
}

/**
 * 返回 Claude Code 当前静默思考阶段的起点。
 *
 * Claude CLI 并不保证在首段文字前发送 thinking heartbeat，因此以 attempt 启动时间
 * 承接静默等待；heartbeat 较晚到达时取两者更早值，避免页面计时倒退。
 */
function liveThinkingStartedAt(
  item: DiscussionRoundParticipant,
  participant: DiscussionParticipant | undefined,
  timeline: DiscussionAttemptTimeline | undefined,
  effectiveStatus: string,
  hasLiveContent: boolean,
): number | null {
  const runtime = timeline?.runtime ?? participant?.runtime;
  if (runtime !== "claude_code" || effectiveStatus !== "running") return null;

  const parsedAttemptStartedAt =
    item.started_at === null ? Number.NaN : Date.parse(item.started_at);
  const attemptStartedAt = Number.isFinite(parsedAttemptStartedAt)
    ? parsedAttemptStartedAt
    : null;
  const nativeStartedAt = timeline?.reducer.meta.thinkingStartedAt ?? null;
  if (timeline?.reducer.phase === "thinking" && nativeStartedAt !== null) {
    return attemptStartedAt === null
      ? nativeStartedAt
      : Math.min(attemptStartedAt, nativeStartedAt);
  }
  return hasLiveContent ? null : attemptStartedAt;
}

/** 生成卡片标题下不暴露连接密钥的运行配置摘要。 */
function participantSubtitle(participant: DiscussionParticipant): string {
  return [participant.effective_model || participant.model, participant.effort]
    .filter(Boolean)
    .join(" · ");
}

function WorkedFold({
  items,
  duration,
  workdir,
  presentation,
}: {
  readonly items: ReturnType<typeof splitCompletedItems>["workItems"];
  readonly duration: string;
  readonly workdir?: string;
  readonly presentation: ReturnType<typeof getExpectedRuntimePresentation> | undefined;
}) {
  return (
    <details className="discussion-worked">
      <summary>
        <svg viewBox="0 0 20 20" aria-hidden="true">
          <circle cx="10" cy="10" r="3" />
          <path d="M10 1.8v2M10 16.2v2M1.8 10h2M16.2 10h2M4.2 4.2l1.4 1.4M14.4 14.4l1.4 1.4M15.8 4.2l-1.4 1.4M5.6 14.4l-1.4 1.4" />
        </svg>
        <span>Worked{duration ? ` for ${duration}` : ""}</span>
        <i aria-hidden="true">⌄</i>
      </summary>
      <div className="discussion-worked__timeline">
        <EventTimeline
          items={items}
          isReplay
          workdir={workdir}
          presentation={presentation}
        />
      </div>
    </details>
  );
}

function footerFacts(item: DiscussionRoundParticipant): string {
  const duration = item.completed_at
    ? formatDuration(item.started_at, item.completed_at)
    : "";
  const toolCount = item.activity?.tool_call_count ?? 0;
  const subagentCount = item.activity?.subagent_count ?? 0;
  return [
    duration,
    `${toolCount} 次工具`,
    subagentCount > 0 ? `${subagentCount} 个子 Agent` : "",
  ]
    .filter(Boolean)
    .join(" · ");
}

function activityTitle(
  activity: DiscussionRoundParticipant["activity"],
): string | undefined {
  if (!activity || activity.tool_call_count === 0) return undefined;
  return Object.entries(activity.tool_names)
    .map(([name, count]) => `${name} × ${count}`)
    .join("、");
}

function statusLabel(status: string): string {
  const labels: Record<string, string> = {
    pending: "等待",
    running: "回答中",
    sealed: "已封口",
    succeeded: "完成",
    failed: "失败",
    limited: "额度不足",
    timed_out: "超时",
    interrupted: "已中断",
    cancelled: "已取消",
    host_lost: "连接中断",
    needs_reconcile: "待恢复",
  };
  return labels[status] ?? status;
}

/** 判断结果槽是否已进入不会自行继续产出事件的终态。 */
function isTerminalResultStatus(status: string): boolean {
  return TERMINAL_RESULT_STATUSES.has(status);
}

function formatDuration(start: string | null, end: string): string {
  if (!start) return "";
  const seconds = Math.max(
    0,
    Math.round((new Date(end).getTime() - new Date(start).getTime()) / 1000),
  );
  return seconds < 60 ? `${seconds} 秒` : `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`;
}

function formatOptionalDuration(
  start: string | null,
  end: string | null,
): string {
  return end ? formatDuration(start, end) : "";
}

/** 优先使用持久时间；轮未共同公开时回退 Agent reducer 的实时耗时。 */
function formatAttemptDuration(
  item: DiscussionRoundParticipant,
  timeline: DiscussionAttemptTimeline | undefined,
): string {
  const persisted = formatOptionalDuration(item.started_at, item.completed_at);
  if (persisted) return persisted;
  const seconds = attemptDurationSeconds(timeline);
  if (seconds === null) return "";
  return seconds < 60
    ? `${seconds} 秒`
    : `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`;
}
