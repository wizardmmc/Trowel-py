import type { ToolItem } from "../../stores/ccStore";

export interface CodexMcpPresentation {
  readonly title: string | null;
  readonly call: string | null;
  readonly result: string | null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function nonEmptyString(value: unknown): string | null {
  return typeof value === "string" && value !== "" ? value : null;
}

export function isCodexMcp(item: ToolItem): boolean {
  return (
    nonEmptyString(item.input.server) !== null &&
    nonEmptyString(item.input.tool) !== null &&
    Object.hasOwn(item.input, "arguments")
  );
}

function callText(argumentsValue: unknown): string | null {
  if (isRecord(argumentsValue)) {
    const code = nonEmptyString(argumentsValue.code);
    if (code !== null) return code;
  }
  if (argumentsValue === null || argumentsValue === undefined) return null;
  try {
    return JSON.stringify(argumentsValue, null, 2);
  } catch {
    return String(argumentsValue);
  }
}

function resultText(value: string | null): string | null {
  if (value === null) return null;
  try {
    const parsed: unknown = JSON.parse(value);
    if (!isRecord(parsed) || !Array.isArray(parsed.content)) return value;
    const hasStructuredData = Object.entries(parsed).some(
      ([key, entry]) => key !== "content" && entry !== null,
    );
    if (hasStructuredData) return value;
    const texts: string[] = [];
    for (const block of parsed.content) {
      if (!isRecord(block) || block.type !== "text") return value;
      const text = nonEmptyString(block.text);
      if (text === null) return value;
      texts.push(text);
    }
    return texts.length > 0 ? texts.join("\n") : value;
  } catch {
    return value;
  }
}

export function getCodexMcpPresentation(
  item: ToolItem,
): CodexMcpPresentation {
  const argumentsValue = item.input.arguments;
  const title = isRecord(argumentsValue)
    ? nonEmptyString(argumentsValue.title)
    : null;
  return {
    title,
    call: callText(argumentsValue),
    result: resultText(item.result),
  };
}
