import type { Processor } from "unified";
import type { Extension } from "micromark-util-types";
import { math } from "micromark-extension-math";
import type { Options } from "micromark-extension-math";
import { mathFromMarkdown, mathToMarkdown } from "mdast-util-math";
import { mathTextGithub } from "./mathTextGithub";

const DOLLAR = 36;

/** unified 的公开 Data 类型没有声明 remark 注册的扩展数组。 */
interface ProcessorData {
  micromarkExtensions?: unknown[];
  fromMarkdownExtensions?: unknown[];
  toMarkdownExtensions?: unknown[];
}

/** 保留上游 block math，只替换 inline `$` tokenizer。 */
function mathGithub(options?: Options | null): Extension {
  const base = math(options);
  return { ...base, text: { [DOLLAR]: mathTextGithub(options) } };
}

export function remarkMathGithub(
  this: Processor,
  options?: Options | null,
): undefined {
  const data = this.data() as unknown as ProcessorData;
  const micromarkExtensions =
    data.micromarkExtensions ?? (data.micromarkExtensions = []);
  const fromMarkdownExtensions =
    data.fromMarkdownExtensions ?? (data.fromMarkdownExtensions = []);
  const toMarkdownExtensions =
    data.toMarkdownExtensions ?? (data.toMarkdownExtensions = []);

  micromarkExtensions.push(mathGithub(options));
  fromMarkdownExtensions.push(mathFromMarkdown());
  toMarkdownExtensions.push(mathToMarkdown());

  return undefined;
}
