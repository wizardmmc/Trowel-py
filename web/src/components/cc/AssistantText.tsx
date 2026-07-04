import React, { type ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

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

/**
 * Render assistant text as markdown (GFM: tables, strikethrough, task lists).
 *
 * Safe by default: react-markdown does not materialize raw HTML without a
 * rehype-raw plugin, so inline `<script>` / `<img onerror>` in assistant text
 * stay escaped text and never become real elements/attributes. Do not add
 * rehype-raw without a sanitization layer.
 */
export function AssistantText({ text }: AssistantTextProps) {
  return (
    <div className="cc-md">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={COMPONENTS}>
        {text}
      </ReactMarkdown>
    </div>
  );
}
