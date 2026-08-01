/** 展示两个 runtime 共用的命令输出、错误和空结果状态。 */

import type { ToolItem } from "../../domain";

export function ToolCommandOutput({ item }: { readonly item: ToolItem }) {
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
