import { useEffect, useRef, useState } from "react";

import type { DiffHunk, WriteDiff } from "../../api/ccTypes";
import type { ToolItem } from "../../stores/ccStore";
import { computeEditDiff, summarizeStat } from "./editDiff";
import { getDisplayPath } from "./pathDisplay";
import { splitBashCommand } from "./bashCommand";
import { getCodexCommandPresentation } from "./codexCommandPresentation";

/**
 * One tool call rendered as a summary line + collapsible detail.
 *
 * slice-029: Edit/MultiEdit/Write get CC-style rendering — the summary line
 * carries a CC verb (Update/Create/Write) plus a `+N −M` stat pill; expanding
 * reveals a line-level red/green diff (Edit/MultiEdit, Write-overwrite) or a
 * Wrote-N-lines + first-10-lines preview (Write-create). Bash keeps its
 * command+output layout; every other tool falls back to a JSON tree of input +
 * result. Pass `condensed` to suppress the detail (subagent children).
 *
 * The summary line is the "terminal-style" affordance: `⚙ <verb> <摘要>
 * · +N −M ✓ <耗时>` at a glance, detail on click.
 */
interface ToolBlockProps {
  readonly item: ToolItem;
  /** Subagent children render condensed: only the summary line, no expandable
   * detail. Avoids a long chain of sub-agent diffs drowning the timeline. */
  readonly condensed?: boolean;
  /** The session's cwd — when a tool's file_path lives inside it, the summary
   * shows the project-relative path (CC `getDisplayPath` semantics) instead of
   * the full absolute path. Omit → always absolute (tests, isolated renders). */
  readonly workdir?: string;
  /** Render native Read/List/Search rows inside a Codex exploration cluster. */
  readonly codexExploration?: boolean;
}

const WRITE_PREVIEW_LINES = 10; // CC `MAX_LINES_TO_RENDER`

function isEditTool(name: string): boolean {
  return name === "Edit" || name === "MultiEdit";
}

/** Tools that produce a diff/create-view in the detail panel. */
function isDiffTool(name: string): boolean {
  return isEditTool(name) || name === "Write";
}

function asString(v: unknown): string | null {
  return typeof v === "string" ? v : null;
}

/** Map a tool_use to its CC-style display verb (FileEditTool/UI.tsx +
 * FileWriteTool/UI.tsx::userFacingName). */
function displayVerb(item: ToolItem): string {
  if (isEditTool(item.toolName)) {
    const oldStr = item.input.old_string;
    return typeof oldStr === "string" && oldStr === "" ? "Create" : "Update";
  }
  if (item.toolName === "Write") return "Write";
  if (item.toolName === "command") {
    if (item.status === "failed") return "Failed";
    return item.status === "running" ? "Running" : "Ran";
  }
  return item.toolName;
}

/** Lines in a string; trailing newline is a terminator, matching CC countLines. */
function countLines(s: string): number {
  if (s === "") return 0;
  const parts = s.split("\n");
  return s.endsWith("\n") ? parts.length - 1 : parts.length;
}

/** Stat for the summary pill, or null when not applicable. Edit/MultiEdit diff
 * is FE-computed; Write pulls from `writeDiff` (BE) or counts content lines. */
function summaryStat(item: ToolItem): { add: number; remove: number } | null {
  if (item.status !== "done") return null;
  if (isEditTool(item.toolName)) {
    // slice-033 feat 2: prefer cc's own patch (real file line numbers) over
    // the FE fragment diff; fall back to fragment when BE attached no writeDiff.
    const wd = item.writeDiff;
    if (wd && wd.type === "update") return summarizeStat(wd.hunks);
    const d = computeEditDiff(item.input);
    return d === null ? null : { add: d.add, remove: d.remove };
  }
  if (item.toolName === "Write") {
    const wd = item.writeDiff;
    if (wd && wd.type === "update") return summarizeStat(wd.hunks);
    const content = asString(item.input.content);
    if (content === null) return null;
    return { add: countLines(content), remove: 0 };
  }
  return null;
}

/** Read-only line count for the summary pill. Read's result is CC `cat -n`
 * text, so its line count equals the number of lines read. Returns null when
 * not applicable (not Read, not done, or no result yet). */
function summaryLines(item: ToolItem): number | null {
  if (item.toolName !== "Read" || item.status !== "done") return null;
  if (item.result === null) return null;
  return countLines(item.result);
}

function brief(text: string, max = 48): string {
  const oneLine = text.replace(/\s+/g, " ").trim();
  return oneLine.length > max ? oneLine.slice(0, max - 1) + "…" : oneLine;
}

/** "Added N lines, Removed M lines" — matches CC FileEditToolUpdatedMessage
 * phrasing including singular/plural and capitalization. */
function statSentence(add: number, remove: number): string {
  const a =
    add > 0 ? `Added ${add} ${add === 1 ? "line" : "lines"}` : "";
  const r =
    remove > 0
      ? `${add === 0 ? "R" : "r"}emoved ${remove} ${remove === 1 ? "line" : "lines"}`
      : "";
  return [a, r].filter(Boolean).join(", ");
}

function SummaryBrief({
  item,
  workdir,
}: {
  readonly item: ToolItem;
  readonly workdir?: string;
}) {
  // slice-034 feat 4: Skill 工具显示加载了哪个 skill，用与 Bash/Read 同款 brief 样式。
  if (item.toolName === "Skill") {
    const skill = asString(item.input.skill);
    return skill !== null ? (
      <span className="cc-tool__brief">加载 skill: {skill}</span>
    ) : null;
  }
  if (item.toolName === "Write" || isEditTool(item.toolName)) {
    const p = asString(item.input.file_path);
    // slice-029: CC-style display — project-relative when inside the session
    // workdir, else absolute. No char-based ellipsis (the truncation hid the
    // useful tail). Long paths wrap via .cc-tool__summary{flex-wrap:wrap}.
    return p !== null ? (
      <span className="cc-tool__brief">{getDisplayPath(p, workdir)}</span>
    ) : null;
  }
  if (item.toolName === "Read") {
    const p = asString(item.input.file_path);
    // Same display logic as Write/Edit (slice-029): project-relative when
    // inside workdir, else absolute. No char-based ellipsis — long paths
    // wrap via .cc-tool__summary{flex-wrap:wrap}.
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
  return null;
}

function CodexActionRows({ item, workdir }: { readonly item: ToolItem; readonly workdir?: string }) {
  const rows = getCodexCommandPresentation(item, workdir).rows;
  return (
    <span className="cc-tool__action-rows">
      {rows.map((row, index) => (
        <span className="cc-tool__action-row" key={`${row.verb}-${index}`}>
          <span className="cc-tool__name">{row.verb}</span>
          <span className="cc-tool__brief" title={row.detail}>{row.detail}</span>
        </span>
      ))}
    </span>
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

/** One diff hunk rendered as gutter+marker+content lines (git-style). */
function DiffHunkView({ hunk }: { readonly hunk: DiffHunk }) {
  const rows: React.ReactNode[] = [];
  let oldLn = hunk.oldStart;
  let newLn = hunk.newStart;
  hunk.lines.forEach((raw, i) => {
    // jsdiff emits "\\ No newline at end of file" markers — skip them (they
    // aren't real content lines and would desync the gutter counters).
    if (raw.startsWith("\\")) return;
    const marker = raw.charAt(0);
    const content = raw.slice(1);
    let type: "add" | "remove" | "context";
    let gutter: number | string;
    if (marker === "+") {
      type = "add";
      gutter = newLn++;
    } else if (marker === "-") {
      type = "remove";
      gutter = oldLn++;
    } else {
      type = "context";
      gutter = oldLn;
      oldLn++;
      newLn++;
    }
    rows.push(
      <div className="cc-tool__diff-line" data-type={type} key={i}>
        <span className="cc-tool__diff-gutter">{gutter}</span>
        <span className="cc-tool__diff-marker">
          {type === "add" ? "+" : type === "remove" ? "−" : " "}
        </span>
        <span className="cc-tool__diff-content">{content}</span>
      </div>,
    );
  });
  return <div className="cc-tool__diff-hunk">{rows}</div>;
}

/** Stat sentence + diff hunks for Edit/MultiEdit/Write-overwrite. */
function DiffBody({
  hunks,
  add,
  remove,
}: {
  readonly hunks: readonly DiffHunk[];
  readonly add: number;
  readonly remove: number;
}) {
  return (
    <>
      <div className="cc-tool__diff-stat">{statSentence(add, remove)}</div>
      <div className="cc-tool__diff">
        {hunks.map((h, i) => (
          <div key={i}>
            <DiffHunkView hunk={h} />
            {i < hunks.length - 1 && <div className="cc-tool__diff-sep">···</div>}
          </div>
        ))}
      </div>
    </>
  );
}

/** Write-create preview: "Wrote N lines" + first 10 lines all-green + more. */
function CreateBody({
  content,
  filePath,
}: {
  readonly content: string;
  readonly filePath: string;
}) {
  const lines = content.split("\n");
  // Trailing newline = terminator → drop the empty trailing element.
  if (lines.length > 0 && lines[lines.length - 1] === "") lines.pop();
  const total = lines.length;
  const shown = lines.slice(0, WRITE_PREVIEW_LINES);
  const more = total - shown.length;
  const name = filePath.split("/").pop() || filePath;
  return (
    <>
      <div className="cc-tool__create-lines">
        Wrote <b>{total}</b> lines to <b>{name}</b>
      </div>
      <div className="cc-tool__diff">
        {shown.map((line, i) => (
          <div className="cc-tool__diff-line" data-type="add" key={i}>
            <span className="cc-tool__diff-gutter">{i + 1}</span>
            <span className="cc-tool__diff-marker">+</span>
            <span className="cc-tool__diff-content">{line}</span>
          </div>
        ))}
        {more > 0 && (
          <div className="cc-tool__create-more">… +{more} more lines</div>
        )}
      </div>
    </>
  );
}

function JsonTree({
  label,
  data,
}: {
  readonly label: string;
  readonly data: unknown;
}) {
  return (
    <div className="cc-tool__json">
      <span className="cc-tool__json-label">{label}</span>
      <pre className="cc-tool__json-body">{JSON.stringify(data, null, 2)}</pre>
    </div>
  );
}

/** Parse CC `cat -n` text into {num, content} rows. Returns null when any
 * line is not cat -n shaped, so the caller falls back to a plain <pre>. CC
 * format — leading spaces + digits + tab + content — was verified against a
 * real Read tool_result in the project session JSONL; line numbers are the
 * real file line numbers (an offset=200 read already carries 200/201/...). */
function parseCatN(
  text: string,
): readonly { readonly num: string; readonly content: string }[] | null {
  // Normalize CRLF → LF first: the line regex anchors on `$`, which does not
  // match a trailing `\r`, so Windows-line-ending files would otherwise fail
  // to parse every line and silently fall back to <pre>.
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  // Trailing newline is a terminator → drop the empty trailing element.
  if (lines.length > 0 && lines[lines.length - 1] === "") lines.pop();
  if (lines.length === 0) return null;
  const rows: { num: string; content: string }[] = [];
  for (const line of lines) {
    const m = /^(\s*)(\d+)\t(.*)$/.exec(line);
    if (m === null) return null;
    rows.push({ num: m[2], content: m[3] });
  }
  return rows;
}

/** Read detail: gutter + content grid when the result is cat -n text, else a
 * plain <pre> fallback (errors, non-file reads). No +/- markers — Read is
 * pure content; line numbers come from CC verbatim, so offset reads keep
 * their real file line numbers. */
function ReadBody({ result }: { readonly result: string }) {
  const rows = parseCatN(result);
  if (rows === null) {
    return <pre className="cc-tool__bash-out">{result}</pre>;
  }
  return (
    <div className="cc-tool__read">
      {rows.map((r) => (
        <div className="cc-tool__read-line" key={r.num}>
          <span className="cc-tool__read-gutter">{r.num}</span>
          <span className="cc-tool__read-content">{r.content}</span>
        </div>
      ))}
    </div>
  );
}

/** Bash command detail (slice-033 feat 4, 方案 A): split a multi-statement
 * command at top-level `;`/`&&`/`||`/`|` so each statement gets its own line
 * instead of one long wrapped line; the separator is dimmed to set it off. A
 * single-statement command renders unchanged (no point splitting one line). */
function BashCommandView({ command }: { readonly command: string }) {
  const segments = splitBashCommand(command);
  if (segments.length <= 1) {
    return <pre className="cc-tool__bash-cmd">{command}</pre>;
  }
  return (
    <pre className="cc-tool__bash-cmd">
      {segments.map((seg, i) => (
        <span key={i}>
          {seg.sep !== "" && (
            <span className="cc-tool__bash-sep">{seg.sep} </span>
          )}
          {seg.body}
          {"\n"}
        </span>
      ))}
    </pre>
  );
}

/** slice-074: Codex commandExecution tools are named "command" by the adapter
 * and behave like CC's Bash (shell command + output). Treat both the same. */
function isCommandTool(name: string): boolean {
  return name === "Bash" || name === "command";
}

/** slice-074: Codex command exit/duration/cwd meta line (gpt5.6 Warning 3). */
function CommandMeta({ item, workdir }: { readonly item: ToolItem; readonly workdir?: string }) {
  if (!isCommandTool(item.toolName)) return null;
  const parts: string[] = [];
  if (typeof item.exitCode === "number") parts.push(`exit ${item.exitCode}`);
  if (typeof item.durationMs === "number") parts.push(`${item.durationMs}ms`);
  if (typeof item.cwd === "string" && item.cwd) {
    parts.push(getDisplayPath(item.cwd, workdir) || ".");
  }
  if (parts.length === 0) return null;
  return <div className="cc-tool__cmd-meta">{parts.join(" · ")}</div>;
}

function CommandOutput({ item }: { readonly item: ToolItem }) {
  if (item.result === null) return null;
  const lines = item.result.replace(/\r\n/g, "\n").split("\n");
  if (lines.length > 0 && lines[lines.length - 1] === "") lines.pop();
  if (item.status === "failed") {
    const tail = lines.slice(-6);
    const omitted = lines.length - tail.length;
    return (
      <pre className="cc-tool__bash-out cc-tool__bash-out--failed">
        {omitted > 0 && `… ${omitted} earlier lines omitted\n`}
        {tail.join("\n")}
      </pre>
    );
  }
  if (lines.length <= 24) return <pre className="cc-tool__bash-out">{item.result}</pre>;
  const omitted = lines.length - 24;
  return (
    <pre className="cc-tool__bash-out">
      {lines.slice(0, 12).join("\n")}
      {`\n… ${omitted} lines omitted …\n`}
      {lines.slice(-12).join("\n")}
    </pre>
  );
}

function CopyButton({ label, text }: { readonly label: string; readonly text: string }) {
  const copy = async () => {
    try {
      await navigator.clipboard?.writeText(text);
    } catch {
      // Clipboard permission is browser-owned. The command/output remains
      // visible and selectable when copying is unavailable.
    }
  };
  return (
    <button type="button" className="cc-tool__copy" onClick={() => void copy()}>
      {label}
    </button>
  );
}

/** Decide what the expanded detail shows. Returns null if there's nothing. */
function renderDetail(item: ToolItem, workdir?: string): React.ReactNode {
  if (isCommandTool(item.toolName)) {
    const command = asString(item.input.command) ?? "";
    return (
      <>
        <BashCommandView command={command} />
        {item.toolName === "command" ? (
          <CommandOutput item={item} />
        ) : item.result !== null ? (
          <pre className="cc-tool__bash-out">{item.result}</pre>
        ) : null}
        <CommandMeta item={item} workdir={workdir} />
        {item.toolName === "command" && (command || item.result !== null) && (
          <div className="cc-tool__copy-actions">
            {command && <CopyButton label="复制命令" text={command} />}
            {item.result !== null && <CopyButton label="复制输出" text={item.result} />}
          </div>
        )}
      </>
    );
  }
  if (item.toolName === "Read") {
    return item.result !== null ? <ReadBody result={item.result} /> : null;
  }
  if (isEditTool(item.toolName)) {
    // slice-033 feat 2: prefer cc's patch (real file line numbers); fall back
    // to the FE fragment diff when BE attached no writeDiff.
    const wd = item.writeDiff;
    if (wd && wd.type === "update") {
      const stat = summarizeStat(wd.hunks);
      return <DiffBody hunks={wd.hunks} add={stat.add} remove={stat.remove} />;
    }
    const d = computeEditDiff(item.input);
    if (d !== null) return <DiffBody hunks={d.hunks} add={d.add} remove={d.remove} />;
    // diff not computable (missing input) → fall through to JSON
  }
  if (item.toolName === "Write") {
    const content = asString(item.input.content);
    const filePath = asString(item.input.file_path) ?? "";
    const wd: WriteDiff | undefined = item.writeDiff;
    if (wd && wd.type === "update") {
      const stat = summarizeStat(wd.hunks);
      return <DiffBody hunks={wd.hunks} add={stat.add} remove={stat.remove} />;
    }
    if (content !== null && filePath !== "") {
      return <CreateBody content={content} filePath={filePath} />;
    }
    // malformed Write input → fall through to JSON
  }
  return (
    <>
      <JsonTree label="input" data={item.input} />
      {item.result !== null && <JsonTree label="result" data={item.result} />}
    </>
  );
}

export function ToolBlock({
  item,
  condensed = false,
  workdir,
  codexExploration = false,
}: ToolBlockProps) {
  const done = item.status === "done";
  const failed = item.status === "failed"; // slice-074: Codex command failure
  const codexCommand = item.toolName === "command";
  // slice-033 feat 3: diff tools (Edit/MultiEdit/Write) auto-expand on done
  // (running collapses — the cc patch arrives on tool_result). Failed Codex
  // commands also auto-expand. Derive the automatic state during render rather
  // than synchronously setting state in an effect; a user click becomes an
  // explicit override and therefore remains sticky.
  const autoOpen = (isDiffTool(item.toolName) && done) || (codexCommand && failed);
  const [openOverride, setOpenOverride] = useState<boolean | null>(null);
  const open = openOverride ?? autoOpen;
  // slice-035 bug2: when a diff tool auto-expands (open flips false→true),
  // scroll the block into view so the diff's top stays visible — otherwise
  // MessageList's stick-to-bottom leaves the just-opened diff head clipped by
  // the Composer. Only the flip triggers a scroll: initial mount with open=true
  // (reloaded history, Write-create) is skipped to avoid a burst of competing
  // scrolls. rAF defers past MessageList's own scroll this render.
  const rootRef = useRef<HTMLDivElement>(null);
  const prevOpenRef = useRef(open);
  const mountedRef = useRef(false);
  useEffect(() => {
    // Skip the very first run: open may start true (reloaded history with
    // done tools, or Write-create) and we must NOT scroll on mount — only on
    // a real false→true flip (an Edit tool transitioning to done live). The
    // mount guard also defends any finalize active→done timing edge.
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
    item.elapsedSeconds !== null ? `${item.elapsedSeconds.toFixed(1)}s` : null;
  const stat = summaryStat(item);
  const lines = summaryLines(item);
  const verb = displayVerb(item);
  const expanded = open && !condensed;

  const summaryInner = (
    <>
      {codexCommand ? (
        <span className="cc-tool__codex-dot" data-state={failed ? "failed" : done ? "done" : "running"} aria-hidden="true" />
      ) : (
        <svg className="cc-tool__icon" viewBox="0 0 24 24" aria-hidden="true">
          <circle cx="12" cy="12" r="3" />
          <path d="M12 2v3M12 19v3M2 12h3M19 12h3M5 5l2 2M17 17l2 2M19 5l-2 2M7 17l-2 2" />
        </svg>
      )}
      {codexCommand && codexExploration ? (
        <CodexActionRows item={item} workdir={workdir} />
      ) : (
        <>
          <span className="cc-tool__name">{verb}</span>
          <SummaryBrief item={item} workdir={workdir} />
        </>
      )}
      {stat !== null && <StatPill stat={stat} />}
      {lines !== null && <span className="cc-tool__stat">{lines} lines</span>}
      {!done && !failed && (isDiffTool(item.toolName) || item.toolName === "Read" || codexCommand) && (
        <span className="cc-tool__spinner" aria-label="进行中" />
      )}
      {failed && typeof item.exitCode === "number" && (
        <span className="cc-tool__exit">exit {item.exitCode}</span>
      )}
      {failed && (
        <span className="cc-tool__check cc-tool__check--failed" aria-label="失败">
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M6 6l12 12M18 6L6 18" />
          </svg>
          {seconds && <span className="cc-tool__elapsed">{seconds}</span>}
        </span>
      )}
      {done && !codexCommand && (
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
      {done && codexCommand && <span className="cc-tool__sr-only" aria-label="完成">完成</span>}
    </>
  );

  // slice-029: condensed (subagent children) renders a non-interactive <div> —
  // a disabled <button> would inherit the browser's grayed-out disabled style
  // and fight the .cc-tool__summary palette.
  const summary = condensed ? (
    <div className="cc-tool__summary cc-tool__summary--condensed">{summaryInner}</div>
  ) : (
    <button
      type="button"
      className="cc-tool__summary"
      onClick={() => setOpenOverride(!open)}
      aria-expanded={open}
      title={codexCommand ? getCodexCommandPresentation(item, workdir).fullCommand : undefined}
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
    >
      {summary}
      {expanded && <div className="cc-tool__detail">{renderDetail(item, workdir)}</div>}
    </div>
  );
}
