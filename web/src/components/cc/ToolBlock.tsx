import { useEffect, useRef, useState } from "react";

import type { ToolItem } from "../../stores/ccStore";
import { getDisplayPath } from "./pathDisplay";
import { getCodexCommandPresentation } from "./codexCommandPresentation";
import {
  getCodexMcpPresentation,
  isCodexMcp,
} from "./codexMcpPresentation";
import { ToolDetail } from "./ToolDetail";
import {
  asString,
  brief,
  displayVerb,
  isDiffTool,
  isEditTool,
  summaryLines,
  summaryStat,
} from "./toolPresentation";

interface ToolBlockProps {
  readonly item: ToolItem;
  readonly condensed?: boolean;
  readonly workdir?: string;
  readonly codexExploration?: boolean;
}

function SummaryBrief({
  item,
  workdir,
}: {
  readonly item: ToolItem;
  readonly workdir?: string;
}) {
  if (item.toolName === "Skill") {
    const skill = asString(item.input.skill);
    return skill !== null ? (
      <span className="cc-tool__brief">加载 skill: {skill}</span>
    ) : null;
  }
  if (item.toolName === "Write" || isEditTool(item.toolName)) {
    const p = asString(item.input.file_path);
    return p !== null ? (
      <span className="cc-tool__brief">{getDisplayPath(p, workdir)}</span>
    ) : null;
  }
  if (item.toolName === "apply_patch") {
    const paths = item.input.paths;
    const p = Array.isArray(paths) && typeof paths[0] === "string" ? paths[0] : null;
    return p !== null ? (
      <span className="cc-tool__brief">{getDisplayPath(p, workdir)}</span>
    ) : null;
  }
  if (item.toolName === "Read") {
    const p = asString(item.input.file_path);
    return p !== null ? (
      <span className="cc-tool__brief">{getDisplayPath(p, workdir)}</span>
    ) : null;
  }
  if (item.toolName === "Bash") {
    const cmd = asString(item.input.command);
    return cmd !== null ? (
      <code className="cc-tool__brief cc-tool__brief--mono">{brief(cmd, 60)}</code>
    ) : null;
  }
  if (item.toolName === "command") {
    const row = getCodexCommandPresentation(item, workdir).rows[0];
    return row.detail ? (
      <code className="cc-tool__brief cc-tool__brief--mono">{brief(row.detail, 72)}</code>
    ) : null;
  }
  if (isCodexMcp(item)) {
    const title = getCodexMcpPresentation(item).title;
    return title !== null ? (
      <span className="cc-tool__brief">{title}</span>
    ) : null;
  }
  return null;
}

function CodexActionRows({ item, workdir }: { readonly item: ToolItem; readonly workdir?: string }) {
  const rows = getCodexCommandPresentation(item, workdir).rows;
  return (
    <div className="cc-tool__action-rows">
      {rows.map((row, index) => (
        <div className="cc-tool__action-row" key={`${row.verb}-${index}`}>
          <span className="cc-tool__name">{row.verb}</span>
          <span className="cc-tool__brief" title={row.detail}>{row.detail}</span>
        </div>
      ))}
    </div>
  );
}

function StatPill({
  stat,
}: {
  readonly stat: { add: number; remove: number };
}) {
  return (
    <span className="cc-tool__stat">
      {stat.add > 0 && <span className="cc-tool__stat-add">+{stat.add}</span>}
      {stat.remove > 0 && <span className="cc-tool__stat-rm">−{stat.remove}</span>}
    </span>
  );
}

export function ToolBlock({
  item,
  condensed = false,
  workdir,
  codexExploration = false,
}: ToolBlockProps) {
  const done = item.status === "done";
  const failed = item.status === "failed";
  const codexCommand = item.toolName === "command";
  const codexMcp = isCodexMcp(item);
  const codexNative = codexCommand || codexMcp;
  const commandPresentation = codexCommand
    ? getCodexCommandPresentation(item, workdir)
    : null;
  const mcpPresentation = codexMcp ? getCodexMcpPresentation(item) : null;
  const autoOpen =
    (isDiffTool(item.toolName) && done) ||
    (codexCommand && (failed || codexExploration)) ||
    (codexMcp && failed);
  const [openOverride, setOpenOverride] = useState<boolean | null>(null);
  const open = openOverride ?? autoOpen;
  const rootRef = useRef<HTMLDivElement>(null);
  const prevOpenRef = useRef(open);
  const mountedRef = useRef(false);
  useEffect(() => {
    if (!mountedRef.current) {
      mountedRef.current = true;
      prevOpenRef.current = open;
      return;
    }
    if (!prevOpenRef.current && open) {
      const el = rootRef.current;
      if (el) {
        requestAnimationFrame(() => {
          if (typeof el.scrollIntoView === "function") {
            el.scrollIntoView({ block: "nearest", behavior: "smooth" });
          }
        });
      }
    }
    prevOpenRef.current = open;
  }, [open]);
  const seconds =
    item.elapsedSeconds !== null
      ? `${item.elapsedSeconds.toFixed(codexMcp ? 2 : 1)}s`
      : codexMcp && typeof item.durationMs === "number"
        ? `${(item.durationMs / 1000).toFixed(2)}s`
        : null;
  const stat = summaryStat(item);
  const lines = summaryLines(item);
  const verb = displayVerb(item);
  const expanded = open && !condensed;

  const summaryInner = (
    <>
      {codexNative ? (
        <span className="cc-tool__codex-dot" data-state={failed ? "failed" : done ? "done" : "running"} aria-hidden="true" />
      ) : (
        <svg className="cc-tool__icon" viewBox="0 0 24 24" aria-hidden="true">
          <circle cx="12" cy="12" r="3" />
          <path d="M12 2v3M12 19v3M2 12h3M19 12h3M5 5l2 2M17 17l2 2M19 5l-2 2M7 17l-2 2" />
        </svg>
      )}
      {codexCommand && codexExploration ? (
        <>
          <span className="cc-tool__name">
            {commandPresentation?.callLabel}
          </span>
          <span
            className="cc-tool__brief"
            title={commandPresentation?.callBrief}
          >
            {commandPresentation?.callBrief}
          </span>
        </>
      ) : (
        <>
          <span
            className={`cc-tool__name${codexMcp ? " cc-tool__name--mono" : ""}`}
          >
            {verb}
          </span>
          <SummaryBrief item={item} workdir={workdir} />
        </>
      )}
      {stat !== null && <StatPill stat={stat} />}
      {lines !== null && <span className="cc-tool__stat">{lines} lines</span>}
      {!done && !failed && (isDiffTool(item.toolName) || item.toolName === "Read" || codexNative) && (
        <span className="cc-tool__spinner" aria-label="进行中" />
      )}
      {failed && typeof item.exitCode === "number" && (
        <span className="cc-tool__exit">exit {item.exitCode}</span>
      )}
      {seconds && codexNative && (!failed || codexMcp) && (
        <span className="cc-tool__elapsed">{seconds}</span>
      )}
      {failed && !codexMcp && (
        <span className="cc-tool__check cc-tool__check--failed" aria-label="失败">
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M6 6l12 12M18 6L6 18" />
          </svg>
          {seconds && <span className="cc-tool__elapsed">{seconds}</span>}
        </span>
      )}
      {done && !codexNative && (
        <span className="cc-tool__check" aria-label="完成">
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M5 13l4 4L19 7" />
          </svg>
          {seconds && <span className="cc-tool__elapsed">{seconds}</span>}
        </span>
      )}
      {!done && !failed && seconds && (
        <span className="cc-tool__elapsed cc-tool__elapsed--running">{seconds}</span>
      )}
      {done && codexNative && <span className="cc-tool__sr-only" aria-label="完成">完成</span>}
      {failed && codexMcp && <span className="cc-tool__sr-only" aria-label="失败">失败</span>}
    </>
  );

  const summary = condensed ? (
    <div className="cc-tool__summary cc-tool__summary--condensed">{summaryInner}</div>
  ) : (
    <button
      type="button"
      className="cc-tool__summary"
      onClick={() => setOpenOverride(!open)}
      aria-expanded={open}
      title={
        codexCommand
          ? commandPresentation?.fullCommand
          : codexMcp
            ? mcpPresentation?.call ?? undefined
            : undefined
      }
    >
      {summaryInner}
    </button>
  );

  return (
    <div
      ref={rootRef}
      className={`cc-tool${codexExploration ? " cc-tool--exploration" : ""}`}
      data-status={item.status}
      data-codex-command={codexCommand || undefined}
      data-codex-native={codexNative || undefined}
    >
      {summary}
      {expanded && (
        <div className="cc-tool__detail">
          {codexExploration && (
            <CodexActionRows item={item} workdir={workdir} />
          )}
          {(!codexExploration || failed) && (
            <ToolDetail item={item} workdir={workdir} />
          )}
        </div>
      )}
    </div>
  );
}
