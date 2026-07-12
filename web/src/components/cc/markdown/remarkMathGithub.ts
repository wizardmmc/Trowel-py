/**
 * remark plugin: math support with the GitHub-style inline-math rule.
 *
 * Reuses the upstream block-math tokenizer (`mathFlow`, via `math()`) and
 * swaps in our patched inline tokenizer (`mathTextGithub`) that rejects a
 * closing `$` preceded by whitespace. See `mathTextGithub.ts` for the why.
 *
 * Only the parse direction is wired (react-markdown never stringifies); the
 * to-markdown extension is included for symmetry with upstream `remark-math`.
 */
import type { Processor } from "unified";
import type { Extension } from "micromark-util-types";
import { math } from "micromark-extension-math";
import type { Options } from "micromark-extension-math";
import { mathFromMarkdown, mathToMarkdown } from "mdast-util-math";
import { mathTextGithub } from "./mathTextGithub";

const DOLLAR = 36;

/**
 * unified `data()` fields that remark/rehype plugins mutate to register
 * micromark/mdast extensions. unified's own `Data` type doesn't declare these
 * (they live in the remark ecosystem), so we narrow via an explicit interface.
 */
interface ProcessorData {
  micromarkExtensions?: unknown[];
  fromMarkdownExtensions?: unknown[];
  toMarkdownExtensions?: unknown[];
}

/**
 * Compose a micromark extension: upstream flow (block `$$`) + patched text
 * (inline `$` with the no-space-before-close rule).
 */
function mathGithub(options?: Options | null): Extension {
  const base = math(options);
  // `base.text[36]` is the stock `mathText`; replace it with our fork. `flow`
  // (block math) is reused unchanged.
  return { ...base, text: { [DOLLAR]: mathTextGithub(options) } };
}

/**
 * Register math parsing/serialization on a unified processor.
 *
 * Args:
 *   options: passed through to the micromark extension (e.g.
 *     `singleDollarTextMath`). Defaults keep single-`$` inline math enabled
 *     (with the GitHub closing rule applied).
 *
 * Returns:
 *   undefined (unified plugin convention).
 */
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
