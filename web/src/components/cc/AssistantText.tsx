import React, { type ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import type { PluggableList } from "unified";
import remarkGfm from "remark-gfm";
import rehypeKatex from "rehype-katex";
import { CodeBlockCopyButton } from "./CodeBlockCopyButton";
import { remarkMathGithub } from "./markdown/remarkMathGithub";

interface AssistantTextProps {
  readonly text: string;
  readonly sessionId?: string;
  readonly workdir?: string;
}

function CodeBlock({ children }: { readonly children?: ReactNode }) {
  let lang: string | undefined;
  const codeChild = Array.isArray(children) ? children[0] : children;
  let codeText = "";
  if (React.isValidElement(codeChild)) {
    const props = codeChild.props as {
      children?: ReactNode;
      className?: unknown;
    };
    const cls = String(props.className ?? "");
    const match = /language-([\w-]+)/.exec(cls);
    lang = match?.[1];
    codeText = React.Children.toArray(props.children).join("");
  }
  if (codeText.endsWith("\n")) codeText = codeText.slice(0, -1);

  return (
    <div className="cc-md-codeblock">
      <div className="cc-md-codeblock__meta">
        {lang && <span className="cc-md-codeblock__lang">{lang}</span>}
        <CodeBlockCopyButton text={codeText} />
      </div>
      <pre>{children}</pre>
    </div>
  );
}

const COMPONENTS: Components = {
  pre: CodeBlock,
};

function localFileUrl(
  href: string,
  sessionId?: string,
  workdir?: string,
): string | null | undefined {
  if (!href.startsWith("/") || href.startsWith("//")) return undefined;
  if (!sessionId || !workdir) return null;

  let decodedHref: string;
  try {
    decodedHref = decodeURI(href);
  } catch {
    return null;
  }
  const root = workdir.replace(/\/+$/, "") || "/";
  const prefix = root === "/" ? "/" : `${root}/`;
  if (!decodedHref.startsWith(prefix)) return null;
  const relativePath = decodedHref.slice(prefix.length);
  if (!relativePath) return null;

  return `/api/agent/sessions/${encodeURIComponent(sessionId)}/files?path=${encodeURIComponent(relativePath)}`;
}

const REMARK_PLUGINS: PluggableList = [remarkGfm, remarkMathGithub];
const REHYPE_PLUGINS: PluggableList = [
  [rehypeKatex, { throwOnError: false, strict: false, errorColor: "#cc0000" }],
];

function AssistantTextView({ text, sessionId, workdir }: AssistantTextProps) {
  const components: Components = {
    ...COMPONENTS,
    a: ({ href, children, ...props }) => {
      const { node, ...anchorProps } = props;
      void node;
      if (!href) {
        return <span title="不支持的链接">{children}</span>;
      }
      const localHref = localFileUrl(href, sessionId, workdir);
      if (localHref === null) {
        return (
          <span
            className="cc-md__blocked-link"
            title="本地路径不在当前会话的工作目录内"
          >
            {children}
          </span>
        );
      }
      return (
        <a
          {...anchorProps}
          href={localHref ?? href}
          target="_blank"
          rel="noopener noreferrer"
        >
          {children}
        </a>
      );
    },
  };

  return (
    <div className="cc-md">
      <ReactMarkdown
        remarkPlugins={REMARK_PLUGINS}
        rehypePlugins={REHYPE_PLUGINS}
        components={components}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}

export const AssistantText = React.memo(AssistantTextView);
