import { useEffect, useRef, useState } from "react";

import { copyText } from "./copyText";

interface CodeBlockCopyButtonProps {
  readonly text: string;
}

export function CodeBlockCopyButton({ text }: CodeBlockCopyButtonProps) {
  const [copiedText, setCopiedText] = useState<string | null>(null);
  const resetTimer = useRef<number | null>(null);
  const copied = copiedText === text;

  useEffect(
    () => () => {
      if (resetTimer.current !== null) window.clearTimeout(resetTimer.current);
    },
    [],
  );

  async function handleCopy(): Promise<void> {
    try {
      await copyText(text);
    } catch {
      return;
    }
    setCopiedText(text);
    if (resetTimer.current !== null) window.clearTimeout(resetTimer.current);
    resetTimer.current = window.setTimeout(() => setCopiedText(null), 1300);
  }

  return (
    <button
      type="button"
      className="cc-md-copy"
      data-state={copied ? "copied" : "idle"}
      onClick={() => void handleCopy()}
      aria-label={copied ? "已复制代码块" : "复制代码块"}
      title={copied ? "已复制" : "复制代码块"}
    >
      {copied ? (
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="m5 12 4 4L19 6" />
        </svg>
      ) : (
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <rect x="9" y="9" width="11" height="11" rx="2" />
          <path d="M15 9V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v7a2 2 0 0 0 2 2h3" />
        </svg>
      )}
    </button>
  );
}
