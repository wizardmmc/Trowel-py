/** 复制原生会话 ID，并显示短时成功反馈。 */

import { useEffect, useRef, useState } from "react";

import { copyText } from "./copyText";

interface SessionIdCopyButtonProps {
  readonly sessionId: string;
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
