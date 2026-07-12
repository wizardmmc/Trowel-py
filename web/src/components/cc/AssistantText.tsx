import React, { type ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import type { PluggableList } from "unified";
import remarkGfm from "remark-gfm";
import rehypeKatex from "rehype-katex";
import { remarkMathGithub } from "./markdown/remarkMathGithub";

interface AssistantTextProps {
  readonly text: string;
}

/**
 * Wrap react-markdown's `<pre>` so the fenced code block's language shows as a
 * corner badge. react-markdown emits `<pre><code class="language-xxx">…</code>
 * </pre>`; we read the code child's className to surface the lang. No syntax
 * highlighting (spec: a later slice) — just the cheap, useful label.
 */
function CodeBlock({ children }: { readonly children?: ReactNode }) {
  let lang: string | undefined;
  const codeChild = Array.isArray(children) ? children[0] : children;
  if (React.isValidElement(codeChild)) {
    const cls = String(
      (codeChild.props as { className?: unknown }).className ?? "",
    );
    const match = /language-([\w-]+)/.exec(cls);
    lang = match?.[1];
  }
  return (
    <div className="cc-md-codeblock">
      {lang && <span className="cc-md-codeblock__lang">{lang}</span>}
      <pre>{children}</pre>
    </div>
  );
}

// Module scope on purpose: a stable `components` reference avoids re-rendering
// ReactMarkdown on every parent render. Moving this inline is a common mistake.
const COMPONENTS: Components = {
  pre: CodeBlock,
};

// Module scope on purpose (same rationale as COMPONENTS above): stable plugin
// arrays avoid re-rendering ReactMarkdown on every parent render.
const REMARK_PLUGINS: PluggableList = [remarkGfm, remarkMathGithub];
const REHYPE_PLUGINS: PluggableList = [
  // throwOnError:false turns KaTeX parse failures into red inline text instead
  // of crashing the whole render; strict:false accepts unicode (CJK fallback)
  // instead of hard-erroring on text-in-math-mode.
  [rehypeKatex, { throwOnError: false, strict: false, errorColor: "#cc0000" }],
];

/**
 * Render assistant text as markdown (GFM + LaTeX via KaTeX).
 *
 * Inline `$x$` and block `$$...$$` render as math. The custom remark plugin
 * (`remarkMathGithub`) applies the GitHub rule that a closing `$` preceded by
 * whitespace is not a math delimiter, so currency like `$100 … $5` is left as
 * plain text instead of being swallowed into a math span.
 *
 * Safe by default: react-markdown does not materialize raw HTML without a
 * rehype-raw plugin, so inline `<script>` / `<img onerror>` in assistant text
 * stay escaped text and never become real elements/attributes. KaTeX runs with
 * `trust` left at its default (false), so it won't execute JS in math either.
 * Do not add rehype-raw without a sanitization layer.
 */
export function AssistantText({ text }: AssistantTextProps) {
  return (
    <div className="cc-md">
      <ReactMarkdown
        remarkPlugins={REMARK_PLUGINS}
        rehypePlugins={REHYPE_PLUGINS}
        components={COMPONENTS}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}
