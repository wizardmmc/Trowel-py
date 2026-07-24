import type { ToolItem } from "../../stores/ccStore";
import { getCodexMcpPresentation } from "./codexMcpPresentation";

export function CodexMcpDetail({ item }: { readonly item: ToolItem }) {
  const presentation = getCodexMcpPresentation(item);
  const result =
    presentation.result ??
    (item.status === "failed" ? "No error details provided" : null);
  return (
    <div
      className={`cc-tool__mcp-detail${item.status === "failed" ? " cc-tool__mcp-detail--failed" : ""}`}
    >
      {result !== null && (
        <div className="cc-tool__mcp-row">
          <span className="cc-tool__mcp-label">
            {item.status === "failed" ? "Error" : "Result"}
          </span>
          <pre className="cc-tool__mcp-value">{result}</pre>
        </div>
      )}
      {presentation.call !== null && (
        <div className="cc-tool__mcp-row">
          <span className="cc-tool__mcp-label">Call</span>
          <pre className="cc-tool__mcp-value cc-tool__mcp-call">
            {presentation.call}
          </pre>
        </div>
      )}
    </div>
  );
}
