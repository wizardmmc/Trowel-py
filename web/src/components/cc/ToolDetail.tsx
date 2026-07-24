import type { ReactNode } from "react";

import type { DiffHunk, WriteDiff } from "../../api/ccTypes";
import type { ToolItem } from "../../stores/ccStore";
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

function DiffHunkView({ hunk }: { readonly hunk: DiffHunk }) {
  const rows: ReactNode[] = [];
  let oldLine = hunk.oldStart;
  let newLine = hunk.newStart;

  hunk.lines.forEach((raw, index) => {
    // jsdiff 的文件末尾标记不是内容行，不能推进 gutter。
    if (raw.startsWith("\\")) return;
    const marker = raw.charAt(0);
    const content = raw.slice(1);
    let type: "add" | "remove" | "context";
    let gutter: number | string;
    if (marker === "+") {
      type = "add";
      gutter = newLine++;
    } else if (marker === "-") {
      type = "remove";
      gutter = oldLine++;
    } else {
      type = "context";
      gutter = oldLine;
      oldLine++;
      newLine++;
    }
    rows.push(
      <div className="cc-tool__diff-line" data-type={type} key={index}>
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
        {hunks.map((hunk, index) => (
          <div key={index}>
            <DiffHunkView hunk={hunk} />
            {index < hunks.length - 1 && (
              <div className="cc-tool__diff-sep">···</div>
            )}
          </div>
        ))}
      </div>
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
  const shown = lines.slice(0, WRITE_PREVIEW_LINES);
  const more = total - shown.length;
  const name = filePath.split("/").pop() || filePath;

  return (
    <>
      <div className="cc-tool__create-lines">
        Wrote <b>{total}</b> lines to <b>{name}</b>
      </div>
      <div className="cc-tool__diff">
        {shown.map((line, index) => (
          <div className="cc-tool__diff-line" data-type="add" key={index}>
            <span className="cc-tool__diff-gutter">{index + 1}</span>
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
