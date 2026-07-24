import type { ToolItem } from "../../stores/ccStore";
import { splitBashCommand } from "./bashCommand";
import { getDisplayPath } from "./pathDisplay";
import { asString } from "./toolPresentation";

export function isCommandTool(name: string): boolean {
  return name === "Bash" || name === "command";
}

function BashCommandView({ command }: { readonly command: string }) {
  const segments = splitBashCommand(command);
  if (segments.length <= 1) {
    return <pre className="cc-tool__bash-cmd">{command}</pre>;
  }
  return (
    <pre className="cc-tool__bash-cmd">
      {segments.map((segment, index) => (
        <span key={index}>
          {segment.sep !== "" && (
            <span className="cc-tool__bash-sep">{segment.sep} </span>
          )}
          {segment.body}
          {"\n"}
        </span>
      ))}
    </pre>
  );
}

function CommandMeta({
  item,
  workdir,
}: {
  readonly item: ToolItem;
  readonly workdir?: string;
}) {
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
  if (lines.length <= 24) {
    return <pre className="cc-tool__bash-out">{item.result}</pre>;
  }
  const omitted = lines.length - 24;
  return (
    <pre className="cc-tool__bash-out">
      {lines.slice(0, 12).join("\n")}
      {`\n… ${omitted} lines omitted …\n`}
      {lines.slice(-12).join("\n")}
    </pre>
  );
}

function CopyButton({
  label,
  text,
}: {
  readonly label: string;
  readonly text: string;
}) {
  const copy = async () => {
    try {
      await navigator.clipboard?.writeText(text);
    } catch {
      // 剪贴板不可用时仍可直接选择可见文本。
    }
  };
  return (
    <button type="button" className="cc-tool__copy" onClick={() => void copy()}>
      {label}
    </button>
  );
}

export function ToolCommandDetail({
  item,
  workdir,
}: {
  readonly item: ToolItem;
  readonly workdir?: string;
}) {
  if (!isCommandTool(item.toolName)) return null;
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
          {item.result !== null && (
            <CopyButton label="复制输出" text={item.result} />
          )}
        </div>
      )}
    </>
  );
}
