import { useEffect, useRef, useState } from "react";

interface SessionIdCopyButtonProps {
  readonly sessionId: string;
}

async function copyText(text: string): Promise<void> {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return;
    } catch {
      // 本地页面的剪贴板权限可能被浏览器策略拦截，继续走同步 fallback。
    }
  }

  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  textarea.focus();
  textarea.select();
  const copied = document.execCommand("copy");
  textarea.remove();
  if (!copied) throw new Error("copy failed");
}

export function SessionIdCopyButton({
  sessionId,
}: SessionIdCopyButtonProps) {
  const [copied, setCopied] = useState(false);
  const resetTimer = useRef<number | null>(null);

  useEffect(
    () => () => {
      if (resetTimer.current !== null) window.clearTimeout(resetTimer.current);
    },
    [],
  );

  async function handleCopy(): Promise<void> {
    try {
      await copyText(sessionId);
    } catch {
      return;
    }
    setCopied(true);
    if (resetTimer.current !== null) window.clearTimeout(resetTimer.current);
    resetTimer.current = window.setTimeout(() => setCopied(false), 1300);
  }

  const action = copied ? "已复制" : "复制";

  return (
    <button
      type="button"
      className="cc-session-id-copy"
      data-state={copied ? "copied" : "idle"}
      onClick={() => void handleCopy()}
      aria-label={`${action}会话 ID ${sessionId}`}
      title={`${action}会话 ID`}
    >
      <svg
        className="cc-session-id-copy__copy"
        viewBox="0 0 24 24"
        aria-hidden="true"
      >
        <rect x="9" y="9" width="11" height="11" rx="2" />
        <path d="M15 9V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v7a2 2 0 0 0 2 2h3" />
      </svg>
      <svg
        className="cc-session-id-copy__check"
        viewBox="0 0 24 24"
        aria-hidden="true"
      >
        <path d="m5 12 4 4L19 6" />
      </svg>
      <span className="cc-session-id-copy__value">{sessionId}</span>
    </button>
  );
}
