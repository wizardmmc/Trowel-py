/** 按共同公开轮次展示稳定参与者顺序、进度和真实失败原因。 */

import { useMemo, useState } from "react";
import { AssistantText } from "../../components/cc/AssistantText";
import type {
  DiscussionParticipant,
  DiscussionRound,
  DiscussionRoundParticipant,
} from "../domain";
import { balancedRows } from "./layout";

interface DiscussionRoundTimelineProps {
  readonly participants: readonly DiscussionParticipant[];
  readonly rounds: readonly DiscussionRound[];
  readonly workdir?: string;
  readonly focusedParticipantId?: string | null;
  readonly onFocusParticipant?: (participantId: string) => void;
  readonly disabled: boolean;
  readonly onMark: (
    roundNumber: number,
    participantId: string,
    marked: boolean,
  ) => void;
}

/** 宽屏使用平衡网格，900px 以下只显示当前参与者的完整卡片。 */
export function DiscussionRoundTimeline({
  participants,
  rounds,
  workdir,
  focusedParticipantId = null,
  onFocusParticipant,
  disabled,
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
          selectedParticipantId={selected}
          focusedParticipantId={focused}
          totalDiscussionParticipants={participants.length}
          workdir={workdir}
          disabled={disabled}
          onMark={onMark}
        />
      ))}
    </>
  );
}

function RoundSection({
  round,
  selectedParticipantId,
  focusedParticipantId,
  totalDiscussionParticipants,
  workdir,
  disabled,
  onMark,
}: {
  readonly round: DiscussionRound;
  readonly selectedParticipantId: string;
  readonly focusedParticipantId: string | null;
  readonly totalDiscussionParticipants: number;
  readonly workdir?: string;
  readonly disabled: boolean;
  readonly onMark: DiscussionRoundTimelineProps["onMark"];
}) {
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
                roundNumber={round.number}
                published={published}
                workdir={workdir}
                selected={item.participant_id === selectedParticipantId}
                dimmed={
                  focusedParticipantId !== null &&
                  item.participant_id !== focusedParticipantId
                }
                disabled={disabled}
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
  roundNumber,
  published,
  workdir,
  selected,
  dimmed,
  disabled,
  onMark,
}: {
  readonly item: DiscussionRoundParticipant;
  readonly roundNumber: number;
  readonly published: boolean;
  readonly workdir?: string;
  readonly selected: boolean;
  readonly dimmed: boolean;
  readonly disabled: boolean;
  readonly onMark: DiscussionRoundTimelineProps["onMark"];
}) {
  const failed = published && item.status !== "succeeded";
  return (
    <article
      className={`discussion-response${selected ? " discussion-response--selected" : ""}${dimmed ? " discussion-response--dimmed" : ""}${failed ? " discussion-response--failed" : ""}`}
      data-participant-id={item.participant_id}
      data-participant-position={item.position % 5}
    >
      <header>
        <span>{item.name.slice(0, 1)}</span>
        <strong>{item.name}</strong>
        <b>{statusLabel(item.status)}</b>
      </header>
      <div className="discussion-response__body">
        {published ? (
          item.content ? (
            <AssistantText text={item.content} workdir={workdir} />
          ) : (
            <p className="discussion-response__error">
              {item.error_message ?? item.error_code ?? statusLabel(item.status)}
            </p>
          )
        ) : (
          <p className="discussion-response__sealed">正文将在本轮共同公开后显示</p>
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

function formatDuration(start: string | null, end: string): string {
  if (!start) return "";
  const seconds = Math.max(
    0,
    Math.round((new Date(end).getTime() - new Date(start).getTime()) / 1000),
  );
  return seconds < 60 ? `${seconds} 秒` : `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`;
}
