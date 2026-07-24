import type { ApprovalDecision } from "../../api/ccTypes";
import type { ApprovalItem } from "../../stores/ccStore";

interface ApprovalBlockProps {
  readonly item: ApprovalItem;
  readonly onDecision?: (requestId: string, decision: string) => void;
  readonly disabled?: boolean;
}

function decisionKey(decision: ApprovalDecision): string | null {
  if (typeof decision === "string") return decision;
  const keys = Object.keys(decision);
  return keys.length === 1 ? keys[0] : null;
}

function decisionLabel(key: string): string | null {
  const labels: Readonly<Record<string, string>> = {
    accept: "仅这次允许",
    acceptForSession: "本会话允许",
    acceptWithExecpolicyAmendment: "允许同类命令",
    decline: "拒绝并继续",
    cancel: "取消本轮",
  };
  return labels[key] ?? null;
}

function TerminalApproval({ item }: { readonly item: ApprovalItem }) {
  let title = "许可已处理";
  let detail = item.decision ?? "已结束";
  if (item.status === "expired") {
    title = "审批已过期";
    detail = "trowel 已安全拒绝，旧请求不能再次回答";
  } else if (item.status === "host_closed") {
    title = "Host 已关闭";
    detail = "连接 generation 已变化，旧请求永久只读";
  } else if (item.autoResolved && item.approvalKind === "file_approval") {
    title = "文件修改已自动安全拒绝";
    detail = "原生请求没有路径、diff 或可选 decision";
  } else if (item.autoResolved && item.approvalKind === "unknown") {
    title = "未知请求已安全拒绝";
    detail = item.resolutionReason ?? "trowel 不支持这个请求类型";
  } else if (item.decision === "accept") {
    title = "已允许这一次";
  } else if (item.decision === "acceptForSession") {
    title = "本会话已允许";
  } else if (item.decision === "acceptWithExecpolicyAmendment") {
    title = "已允许同类命令";
  } else if (item.decision === "decline") {
    title = "已拒绝并继续";
  } else if (item.decision === "cancel") {
    title = "已取消本轮";
  }
  return (
    <div className="cc-approval cc-approval--terminal" data-status={item.status}>
      <div className="cc-approval__head">
        <b>{title}</b>
        <span>Codex · {item.approvalKind}</span>
      </div>
      {item.command && <pre className="cc-approval__command">{item.command}</pre>}
      <div className="cc-approval__terminal">
        <span>{detail}</span>
        <code>{item.decision ?? item.status}</code>
      </div>
    </div>
  );
}

export function ApprovalBlock({
  item,
  onDecision,
  disabled = false,
}: ApprovalBlockProps) {
  if (item.status !== "pending") return <TerminalApproval item={item} />;

  const choices = item.availableDecisions
    .map((decision) => {
      const key = decisionKey(decision);
      const label = key === null ? null : decisionLabel(key);
      return key !== null && label !== null ? { key, label } : null;
    })
    .filter((choice): choice is { key: string; label: string } => choice !== null);

  return (
    <section className="cc-approval" aria-label="Codex command approval pending">
      <div className="cc-approval__head">
        <b>需要你的许可</b>
        <span>Codex · {item.approvalKind}</span>
      </div>
      <p className="cc-approval__reason">
        {item.reason ?? "Codex 请求执行一个需要许可的操作。"}
      </p>
      {item.command && <pre className="cc-approval__command">{item.command}</pre>}
      <dl className="cc-approval__facts">
        <dt>cwd</dt>
        <dd>{item.cwd ?? "未提供"}</dd>
        <dt>request</dt>
        <dd>{item.requestId}</dd>
      </dl>
      <div className="cc-approval__impact">
        <b>影响范围</b>
        <span>命令可能修改工作区外内容；允许规则后，同类命令可能不再询问。</span>
      </div>
      <div className="cc-approval__actions">
        {choices.map(({ key, label }) => (
          <button
            key={key}
            type="button"
            className={`cc-approval__button cc-approval__button--${key}`}
            disabled={disabled}
            onClick={() => onDecision?.(item.requestId, key)}
          >
            {label}
          </button>
        ))}
      </div>
      <div className="cc-approval__native">
        原生 availableDecisions：{choices.map((choice) => choice.key).join(" · ")}
      </div>
    </section>
  );
}
