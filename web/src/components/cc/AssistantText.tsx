import React, { type ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import type { PluggableList } from "unified";
import remarkGfm from "remark-gfm";
import rehypeKatex from "rehype-katex";
import { remarkMathGithub } from "./markdown/remarkMathGithub";

interface AssistantTextProps {
  readonly text: string;
}

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

const COMPONENTS: Components = {
  pre: CodeBlock,
};

const REMARK_PLUGINS: PluggableList = [remarkGfm, remarkMathGithub];
const REHYPE_PLUGINS: PluggableList = [
  [rehypeKatex, { throwOnError: false, strict: false, errorColor: "#cc0000" }],
];

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
