import { Fragment, useMemo, useState, type ReactNode } from "react";

import type { DiffHunk, WriteDiff } from "../../agent/transport";
import type { ToolItem } from "../../agent/domain";
import { CodexMcpDetail } from "./CodexMcpDetail";
import { isCodexMcp } from "./codexMcpPresentation";
import { computeEditDiff, summarizeStat } from "./editDiff";
import {
  isCommandTool,
  ToolCommandDetail,
} from "./ToolCommandDetail";
import {
  asString,
  isEditTool,
  parseCatN,
  statSentence,
} from "./toolPresentation";

const WRITE_PREVIEW_LINES = 10;

interface DiffRow {
  readonly key: string;
  readonly hunkIndex: number;
  readonly type: "add" | "remove" | "context";
  readonly gutter: number;
  readonly content: string;
}

function rowsFromHunks(hunks: readonly DiffHunk[]): readonly DiffRow[] {
  return hunks.flatMap((hunk, hunkIndex) => {
    let oldLine = hunk.oldStart;
    let newLine = hunk.newStart;
    const rows: DiffRow[] = [];
    hunk.lines.forEach((raw, lineIndex) => {
      // jsdiff 的文件末尾标记不是内容行，不能推进 gutter。
      if (raw.startsWith("\\")) return;
      const marker = raw.charAt(0);
      const content = raw.slice(1);
      let type: DiffRow["type"];
      let gutter: number;
      if (marker === "+") {
        type = "add";
        gutter = newLine++;
      } else if (marker === "-") {
        type = "remove";
        gutter = oldLine++;
      } else {
        type = "context";
        gutter = oldLine;
        oldLine += 1;
        newLine += 1;
      }
      rows.push({
        key: `${hunkIndex}:${lineIndex}`,
        hunkIndex,
        type,
        gutter,
        content,
      });
    });
    return rows;
  });
}

function FileDiffPreview({ rows }: { readonly rows: readonly DiffRow[] }) {
  const [expanded, setExpanded] = useState(false);
  const visible = expanded ? rows : rows.slice(0, WRITE_PREVIEW_LINES);
  let previousHunk = visible[0]?.hunkIndex ?? 0;

  return (
    <div className="cc-tool__diff">
      {visible.map((row) => {
        const separated = row.hunkIndex !== previousHunk;
        previousHunk = row.hunkIndex;
        return (
          <Fragment key={row.key}>
            {separated && <div className="cc-tool__diff-sep">···</div>}
            <div className="cc-tool__diff-line" data-type={row.type}>
              <span className="cc-tool__diff-gutter">{row.gutter}</span>
              <span className="cc-tool__diff-marker">
                {row.type === "add" ? "+" : row.type === "remove" ? "−" : " "}
              </span>
              <span className="cc-tool__diff-content">{row.content}</span>
            </div>
          </Fragment>
        );
      })}
      {rows.length > WRITE_PREVIEW_LINES && (
        <button
          type="button"
          className="cc-tool__diff-toggle"
          aria-expanded={expanded}
          onClick={() => setExpanded((value) => !value)}
        >
          <span>
            {expanded
              ? `收起为 ${WRITE_PREVIEW_LINES} 行`
              : `展开全部 ${rows.length} 行`}
          </span>
          <span aria-hidden="true">{expanded ? "⌃" : "⌄"}</span>
        </button>
      )}
    </div>
  );
}

function DiffBody({
  hunks,
  add,
  remove,
}: {
  readonly hunks: readonly DiffHunk[];
  readonly add: number;
  readonly remove: number;
}) {
  const rows = useMemo(() => rowsFromHunks(hunks), [hunks]);
  return (
    <>
      <div className="cc-tool__diff-stat">{statSentence(add, remove)}</div>
      <FileDiffPreview rows={rows} />
    </>
  );
}

function CreateBody({
  content,
  filePath,
}: {
  readonly content: string;
  readonly filePath: string;
}) {
  const lines = content.split("\n");
  if (lines.length > 0 && lines[lines.length - 1] === "") lines.pop();
  const total = lines.length;
  const name = filePath.split("/").pop() || filePath;
  const rows: readonly DiffRow[] = lines.map((line, index) => ({
    key: `create:${index}`,
    hunkIndex: 0,
    type: "add",
    gutter: index + 1,
    content: line,
  }));

  return (
    <>
      <div className="cc-tool__create-lines">
        Wrote <b>{total}</b> lines to <b>{name}</b>
      </div>
      <FileDiffPreview rows={rows} />
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

function ReadBody({ result }: { readonly result: string }) {
  const rows = parseCatN(result);
  if (rows === null) {
    return <pre className="cc-tool__bash-out">{result}</pre>;
  }
  return (
    <div className="cc-tool__read">
      {rows.map((row) => (
        <div className="cc-tool__read-line" key={row.num}>
          <span className="cc-tool__read-gutter">{row.num}</span>
          <span className="cc-tool__read-content">{row.content}</span>
        </div>
      ))}
    </div>
  );
}

function renderDetail(item: ToolItem, workdir?: string): ReactNode {
  if (isCodexMcp(item)) {
    return <CodexMcpDetail item={item} />;
  }
  if (isCommandTool(item.toolName)) {
    return <ToolCommandDetail item={item} workdir={workdir} />;
  }
  if (item.toolName === "Read") {
    return item.result !== null ? <ReadBody result={item.result} /> : null;
  }
  if (isEditTool(item.toolName)) {
    const writeDiff = item.writeDiff;
    if (writeDiff?.type === "update") {
      const stat = summarizeStat(writeDiff.hunks);
      return (
        <DiffBody
          hunks={writeDiff.hunks}
          add={stat.add}
          remove={stat.remove}
        />
      );
    }
    const diff = computeEditDiff(item.input);
    if (diff !== null) {
      return (
        <DiffBody
          hunks={diff.hunks}
          add={diff.add}
          remove={diff.remove}
        />
      );
    }
  }
  if (item.toolName === "Write") {
    const content = asString(item.input.content);
    const filePath = asString(item.input.file_path) ?? "";
    const writeDiff: WriteDiff | undefined = item.writeDiff;
    if (writeDiff?.type === "update") {
      const stat = summarizeStat(writeDiff.hunks);
      return (
        <DiffBody
          hunks={writeDiff.hunks}
          add={stat.add}
          remove={stat.remove}
        />
      );
    }
    if (content !== null && filePath !== "") {
      return <CreateBody content={content} filePath={filePath} />;
    }
  }
  if (item.toolName === "apply_patch") {
    const writeDiff: WriteDiff | undefined = item.writeDiff;
    if (writeDiff && writeDiff.hunks.length > 0) {
      const stat = summarizeStat(writeDiff.hunks);
      return (
        <DiffBody
          hunks={writeDiff.hunks}
          add={stat.add}
          remove={stat.remove}
        />
      );
    }
  }
  return (
    <>
      <JsonTree label="input" data={item.input} />
      {item.result !== null && <JsonTree label="result" data={item.result} />}
    </>
  );
}

export function ToolDetail({
  item,
  workdir,
}: {
  readonly item: ToolItem;
  readonly workdir?: string;
}) {
  return <>{renderDetail(item, workdir)}</>;
}
