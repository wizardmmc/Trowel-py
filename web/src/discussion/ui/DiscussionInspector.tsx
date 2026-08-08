/** 展示冻结参与者、视觉强调入口和研讨进入长期系统的真实边界。 */

import type { Discussion } from "../domain";

interface DiscussionInspectorProps {
  readonly discussion: Discussion;
  readonly focusedParticipantId: string | null;
  readonly onFocusParticipant: (participantId: string) => void;
  readonly overlay: boolean;
  readonly onClose: () => void;
}

/** 检查器只解释服务端快照，不提供创建后修改参与者的入口。 */
export function DiscussionInspector({
  discussion,
  focusedParticipantId,
  onFocusParticipant,
  overlay,
  onClose,
}: DiscussionInspectorProps) {
  return (
    <aside
      className={`discussion-inspector${overlay ? " discussion-inspector--overlay" : ""}`}
      aria-label="参与者状态"
    >
      <header>
        <strong>参与者与记录</strong>
        {overlay && (
          <button type="button" onClick={onClose} aria-label="关闭参与者栏">
            ×
          </button>
        )}
      </header>
      <div className="discussion-inspector__body">
        <section className="discussion-inspector__section discussion-inspector__roster">
          <h2>参与者</h2>
          <div className="discussion-inspector__participants">
            {discussion.participants.map((participant) => (
              <button
                type="button"
                key={participant.id}
                aria-label={`强调 ${participant.name}`}
                aria-pressed={focusedParticipantId === participant.id}
                data-participant-position={participant.position % 5}
                onClick={() => onFocusParticipant(participant.id)}
              >
                <span className="discussion-inspector__participant-top">
                  <i aria-hidden="true" />
                  <strong>{participant.name}</strong>
                  <em data-status={participant.status}>
                    {participantStatus(participant.status)}
                  </em>
                </span>
                <small>
                  {participant.runtime === "codex" ? "Codex" : "Claude Code"}
                  {" · "}
                  {participant.connection_name ?? "未知连接"}
                </small>
                <small
                  className="discussion-inspector__model"
                  title={modelTitle(participant)}
                >
                  {participant.effective_model}
                  {participant.model !== participant.effective_model
                    ? `（${participant.model}）`
                    : ""}
                  {participant.effort ? ` · ${participant.effort}` : ""}
                </small>
                <ContextBadges participant={participant} />
              </button>
            ))}
          </div>
        </section>

        <section className="discussion-inspector__section discussion-inspector__policy">
          <h2>Memory 资格</h2>
          <PolicyRow
            title="Daily / Episode"
            detail="整场只记录一次"
            state="进入"
            enabled
          />
          <PolicyRow title="Note" detail="需标记或后续验证" state="默认关闭" />
          <PolicyRow
            title="Profile"
            detail="只读取用户原话"
            state="用户消息"
            enabled
          />
        </section>

        <section className="discussion-inspector__section discussion-inspector__facts">
          <h2>讨论状态</h2>
          <dl>
            <Fact label="轮次" value={roundFact(discussion)} />
            <Fact
              label="参与者"
              value={`${discussion.participants.length} 个原生会话`}
            />
            <Fact label="用量" value={usageFact(discussion)} />
            <Fact label="标记" value={`${markedCount(discussion)} 条`} />
            <Fact
              label="工作区"
              value={basename(discussion.workdir)}
              title={discussion.workdir}
            />
          </dl>
        </section>
      </div>
    </aside>
  );
}

function modelTitle(
  participant: Discussion["participants"][number],
): string {
  return participant.model === participant.effective_model
    ? `实际模型：${participant.effective_model}`
    : `实际模型：${participant.effective_model}；选择别名：${participant.model}`;
}

function ContextBadges({
  participant,
}: {
  readonly participant: Discussion["participants"][number];
}) {
  return (
    <span className="discussion-context-badges" aria-label="上下文与权限">
      <b data-enabled={participant.memory_enabled}>M</b>
      <b data-enabled={participant.profile_enabled}>P</b>
      <b data-enabled={participant.self_enabled}>S</b>
      <b>{permissionLabel(participant)}</b>
    </span>
  );
}

function PolicyRow({
  title,
  detail,
  state,
  enabled = false,
}: {
  readonly title: string;
  readonly detail: string;
  readonly state: string;
  readonly enabled?: boolean;
}) {
  return (
    <div className="discussion-memory-policy">
      <div>
        <strong>{title}</strong>
        <small>{detail}</small>
      </div>
      <span data-enabled={enabled}>{state}</span>
    </div>
  );
}

function Fact({
  label,
  value,
  title,
}: {
  readonly label: string;
  readonly value: string;
  readonly title?: string;
}) {
  return (
    <div>
      <dt>{label}</dt>
      <dd title={title}>{value}</dd>
    </div>
  );
}

function roundFact(discussion: Discussion): string {
  const current = discussion.rounds.reduce(
    (maximum, round) => Math.max(maximum, round.number),
    0,
  );
  return discussion.progression_mode === "automatic" && discussion.max_rounds !== null
    ? `${current} / ${discussion.max_rounds}`
    : `${current} 轮 · 逐轮决定`;
}

function markedCount(discussion: Discussion): number {
  return discussion.rounds.reduce(
    (total, round) =>
      total + round.participants.filter((item) => item.marked).length,
    0,
  );
}

function usageFact(discussion: Discussion): string {
  const tokens = discussion.rounds.reduce(
    (total, round) =>
      total +
      round.participants.reduce(
        (roundTotal, item) => roundTotal + usageTokens(item.usage),
        0,
      ),
    0,
  );
  if (tokens <= 0) return "尚未回报";
  return tokens >= 1000
    ? `约 ${(tokens / 1000).toFixed(1)}k token`
    : `约 ${tokens} token`;
}

function usageTokens(usage: Readonly<Record<string, unknown>> | null): number {
  if (!usage) return 0;
  for (const key of ["total_tokens", "totalTokens"]) {
    const value = usage[key];
    if (typeof value === "number" && Number.isFinite(value) && value >= 0) {
      return value;
    }
  }
  return (
    numericUsage(usage, "input_tokens", "inputTokens") +
    numericUsage(usage, "output_tokens", "outputTokens")
  );
}

function numericUsage(
  usage: Readonly<Record<string, unknown>>,
  snakeCase: string,
  camelCase: string,
): number {
  const value = usage[snakeCase] ?? usage[camelCase];
  return typeof value === "number" && Number.isFinite(value) && value >= 0
    ? value
    : 0;
}

function permissionLabel(
  participant: Discussion["participants"][number],
): string {
  return participant.runtime === "codex"
    ? (participant.permission_preset ?? "read-only")
    : (participant.permission_mode ?? "dontAsk");
}

function basename(path: string): string {
  return path.split("/").filter(Boolean).at(-1) ?? path;
}

function participantStatus(status: string): string {
  const labels: Record<string, string> = {
    pending: "待命",
    ready: "空闲",
    active: "运行中",
    closed: "已关闭",
    stopped: "已停止",
    needs_reconcile: "待恢复",
  };
  return labels[status] ?? status;
}
