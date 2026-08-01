/** 承载 Codex 状态、Agent、review 和 diff 命令的对话框流程。 */

import { useEffect, useRef, useState } from "react";

import type { CodexReviewTarget } from "../../transport";
import type { CodexTurnDiff, SessionMeta } from "../../domain";

interface CodexCommandSessionState {
  readonly codexSubagents: Readonly<Record<string, {
    readonly threadId: string;
    readonly agentPath: string | null;
    readonly status: string;
  }>>;
  readonly meta: SessionMeta;
  readonly nativeSessionId: string | null;
  readonly effort: string | null;
  readonly permission: string | null;
  readonly effectiveSandbox?: string | null;
  readonly effectiveApproval?: string | null;
  readonly networkAccess?: boolean | null;
  readonly turnDiff: CodexTurnDiff | null;
}

export type CodexCommandDialogKind =
  | "status"
  | "review"
  | "diff"
  | "agent"
  | null;

interface CodexCommandDialogsProps {
  readonly kind: CodexCommandDialogKind;
  readonly active: CodexCommandSessionState | null;
  readonly onClose: () => void;
  readonly onStartReview: (target: CodexReviewTarget) => void;
  readonly reviewPending: boolean;
  readonly reviewError: string | null;
  readonly onLocateSubagent?: (threadId: string) => void;
}

type ReviewTargetType = CodexReviewTarget["type"];

const REVIEW_OPTIONS: readonly {
  readonly value: ReviewTargetType;
  readonly label: string;
  readonly description: string;
}[] = [
  {
    value: "uncommittedChanges",
    label: "未提交改动",
    description: "staged、unstaged 与 untracked 文件",
  },
  {
    value: "baseBranch",
    label: "相对基线分支",
    description: "当前分支相对指定本地分支的改动",
  },
  {
    value: "commit",
    label: "指定 commit",
    description: "指定提交引入的改动",
  },
  {
    value: "custom",
    label: "自定义说明",
    description: "按给定范围和关注点审查",
  },
];

export function CodexCommandDialogs({
  kind,
  active,
  onClose,
  onStartReview,
  reviewPending,
  reviewError,
  onLocateSubagent,
}: CodexCommandDialogsProps) {
  if (kind === null || active === null) return null;
  if (kind === "status") {
    return (
      <CommandModal title="会话状态" ariaLabel="Codex 会话状态" onClose={onClose}>
        <StatusBody active={active} />
      </CommandModal>
    );
  }
  if (kind === "diff") {
    return (
      <CommandModal
        title="当前 turn 的改动"
        ariaLabel="当前 turn 聚合 diff"
        wide
        onClose={onClose}
      >
        <DiffBody active={active} />
      </CommandModal>
    );
  }
  if (kind === "agent") {
    return (
      <CommandModal title="Subagent" ariaLabel="Subagent 定位器" onClose={onClose}>
        <AgentBody
          active={active}
          onLocate={(threadId) => onLocateSubagent?.(threadId)}
        />
      </CommandModal>
    );
  }
  return (
    <CommandModal title="选择审查目标" ariaLabel="选择审查目标" onClose={onClose}>
      <ReviewBody
        pending={reviewPending}
        error={reviewError}
        onStart={onStartReview}
      />
    </CommandModal>
  );
}

function AgentBody({
  active,
  onLocate,
}: {
  readonly active: CodexCommandSessionState;
  readonly onLocate: (threadId: string) => void;
}) {
  const agents = Object.values(active.codexSubagents);
  return (
    <div className="cc-command-modal__body cc-agent-locator">
      {agents.length === 0 ? (
        <div className="cc-command-empty">当前没有 Subagent</div>
      ) : (
        agents.map((agent) => (
          <button
            type="button"
            className="cc-agent-locator__row"
            key={agent.threadId}
            onClick={() => onLocate(agent.threadId)}
          >
            <span className="cc-agent-locator__path">
              {agent.agentPath ?? agent.threadId}
            </span>
            <span className={`cc-agent-locator__status cc-agent-locator__status--${agent.status}`}>
              {agent.status}
            </span>
          </button>
        ))
      )}
    </div>
  );
}

function CommandModal({
  title,
  ariaLabel,
  wide = false,
  onClose,
  children,
}: {
  readonly title: string;
  readonly ariaLabel: string;
  readonly wide?: boolean;
  readonly onClose: () => void;
  readonly children: React.ReactNode;
}) {
  const dialogRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    dialogRef.current?.focus();
    return () => previous?.focus();
  }, []);

  function handleKeyDown(event: React.KeyboardEvent<HTMLDivElement>) {
    if (event.key === "Escape") {
      event.preventDefault();
      onClose();
      return;
    }
    if (event.key !== "Tab") return;
    const focusable = Array.from(
      dialogRef.current?.querySelectorAll<HTMLElement>(
        'button:not(:disabled), input:not(:disabled), textarea:not(:disabled), [tabindex="0"]',
      ) ?? [],
    );
    if (focusable.length === 0) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  return (
    <div
      className="cc-command-modal__backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        ref={dialogRef}
        className={`cc-command-modal${wide ? " cc-command-modal--wide" : ""}`}
        role="dialog"
        aria-modal="true"
        aria-label={ariaLabel}
        tabIndex={-1}
        onKeyDown={handleKeyDown}
      >
        <header className="cc-command-modal__head">
          <h2>{title}</h2>
          <button type="button" onClick={onClose} aria-label="关闭">
            ×
          </button>
        </header>
        {children}
      </div>
    </div>
  );
}

function StatusBody({ active }: { readonly active: CodexCommandSessionState }) {
  const totalTokens = nestedNumber(active.meta.usage?.total, "totalTokens");
  const contextWindow = directNumber(
    active.meta.usage?.model_context_window,
  );
  const contextPercent =
    totalTokens !== null && contextWindow !== null && contextWindow > 0
      ? Math.min(100, (totalTokens / contextWindow) * 100)
      : null;
  const rateWindows = [
    ["primary", active.meta.rateLimit?.primary ?? null],
    ["secondary", active.meta.rateLimit?.secondary ?? null],
  ] as const;

  return (
    <div className="cc-command-modal__body">
      <dl className="cc-command-facts">
        <Fact label="session" value={active.nativeSessionId} />
        <Fact label="model" value={active.meta.model} />
        <Fact label="effort" value={active.effort} />
        <Fact label="permission" value={active.permission} />
        <Fact label="sandbox" value={active.effectiveSandbox} />
        <Fact label="approval" value={active.effectiveApproval} />
        <Fact
          label="network"
          value={
            active.networkAccess === null || active.networkAccess === undefined
              ? null
              : active.networkAccess
                ? "允许"
                : "不允许"
          }
        />
      </dl>
      <section className="cc-command-meter" aria-label="上下文用量">
        <div className="cc-command-meter__head">
          <span>context</span>
          <b>
            {totalTokens !== null && contextWindow !== null
              ? `${formatNumber(totalTokens)} / ${formatNumber(contextWindow)}`
              : "尚未收到用量"}
          </b>
        </div>
        <div className="cc-command-meter__track">
          {contextPercent !== null && <span style={{ width: `${contextPercent}%` }} />}
        </div>
      </section>
      <section className="cc-command-rate" aria-label="速率额度">
        <div className="cc-command-rate__title">rate limit</div>
        {rateWindows.map(([name, window]) =>
          typeof window?.usedPercent === "number" ? (
            <div className="cc-command-rate__row" key={name}>
              <span>{name}</span>
              <div className="cc-command-meter__track">
                <span style={{ width: `${Math.min(100, Math.max(0, window.usedPercent))}%` }} />
              </div>
              <b>{formatNumber(window.usedPercent)}%</b>
            </div>
          ) : null,
        )}
        {!rateWindows.some(([, window]) => typeof window?.usedPercent === "number") && (
          <div className="cc-command-empty">尚未收到额度</div>
        )}
      </section>
    </div>
  );
}

function Fact({ label, value }: { readonly label: string; readonly value: string | null | undefined }) {
  return (
    <div className="cc-command-fact">
      <dt>{label}</dt>
      <dd>{value && value.trim() ? value : "未提供"}</dd>
    </div>
  );
}

function ReviewBody({
  pending,
  error,
  onStart,
}: {
  readonly pending: boolean;
  readonly error: string | null;
  readonly onStart: (target: CodexReviewTarget) => void;
}) {
  const [type, setType] = useState<ReviewTargetType>("uncommittedChanges");
  const [branch, setBranch] = useState("");
  const [sha, setSha] = useState("");
  const [title, setTitle] = useState("");
  const [instructions, setInstructions] = useState("");
  const valid =
    type === "uncommittedChanges" ||
    (type === "baseBranch" && branch.trim().length > 0) ||
    (type === "commit" && sha.trim().length > 0) ||
    (type === "custom" && instructions.trim().length > 0);

  function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!valid || pending) return;
    if (type === "uncommittedChanges") {
      onStart({ type });
    } else if (type === "baseBranch") {
      onStart({ type, branch: branch.trim() });
    } else if (type === "commit") {
      onStart({
        type,
        sha: sha.trim(),
        ...(title.trim() ? { title: title.trim() } : {}),
      });
    } else {
      onStart({ type, instructions: instructions.trim() });
    }
  }

  return (
    <form onSubmit={submit}>
      <div className="cc-command-modal__body cc-review-targets">
        {REVIEW_OPTIONS.map((option) => (
          <label className="cc-review-target" key={option.value}>
            <input
              type="radio"
              name="review-target"
              value={option.value}
              checked={type === option.value}
              onChange={() => setType(option.value)}
              disabled={pending}
            />
            <span>
              <b>{option.label}</b>
              <small>{option.description}</small>
            </span>
          </label>
        ))}
        {type === "baseBranch" && (
          <label className="cc-command-field">
            <span>基线分支</span>
            <input value={branch} onChange={(event) => setBranch(event.target.value)} autoFocus />
          </label>
        )}
        {type === "commit" && (
          <div className="cc-command-fieldset">
            <label className="cc-command-field">
              <span>commit SHA</span>
              <input value={sha} onChange={(event) => setSha(event.target.value)} autoFocus />
            </label>
            <label className="cc-command-field">
              <span>标题（可选）</span>
              <input value={title} onChange={(event) => setTitle(event.target.value)} />
            </label>
          </div>
        )}
        {type === "custom" && (
          <label className="cc-command-field">
            <span>审查说明</span>
            <textarea
              value={instructions}
              onChange={(event) => setInstructions(event.target.value)}
              rows={4}
              autoFocus
            />
          </label>
        )}
        {error && <div className="cc-command-error" role="alert">{error}</div>}
      </div>
      <footer className="cc-command-modal__foot">
        <button type="submit" className="cc-command-primary" disabled={!valid || pending}>
          {pending ? "正在启动…" : "开始审查"}
        </button>
      </footer>
    </form>
  );
}

function DiffBody({ active }: { readonly active: CodexCommandSessionState }) {
  const snapshot = active.turnDiff;
  return (
    <div className="cc-command-modal__body cc-command-diff-body">
      {!snapshot || snapshot.diff.trim().length === 0 ? (
        <div className="cc-command-empty">当前 turn 还没有聚合 diff</div>
      ) : (
        <>
          <div className="cc-command-diff-meta">turn {snapshot.turnId}</div>
          <pre className="cc-command-diff">
            {snapshot.diff.split("\n").map((line, index) => (
              <span className={diffLineClass(line)} key={`${index}:${line}`}>{line || " "}</span>
            ))}
          </pre>
        </>
      )}
    </div>
  );
}

function diffLineClass(line: string): string {
  if (line.startsWith("+++ ") || line.startsWith("--- ")) return "cc-command-diff__file";
  if (line.startsWith("@@")) return "cc-command-diff__hunk";
  if (line.startsWith("+")) return "cc-command-diff__add";
  if (line.startsWith("-")) return "cc-command-diff__del";
  return "";
}

function directNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function nestedNumber(value: unknown, key: string): number | null {
  if (!value || typeof value !== "object") return null;
  return directNumber((value as Record<string, unknown>)[key]);
}

function formatNumber(value: number): string {
  return value.toLocaleString("en-US", { maximumFractionDigits: 1 });
}
