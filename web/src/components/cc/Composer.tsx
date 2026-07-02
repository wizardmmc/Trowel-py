import { useEffect, useRef, useState } from "react";

/**
 * The message composer. Esc follows the CC-terminal convention with three
 * context-specific behaviors (most-specific first):
 *   1. if any collapsible is open anywhere in the view → close it (handled
 *      by those components' own state on blur; here we only own input/turn)
 *   2. if the input has text → clear it
 *   3. if a turn is streaming → interrupt
 * Enter sends, Shift+Enter inserts a newline. Slash input is NOT intercepted —
 * it is posted raw so the backend input layer can fire skills / expand custom
 * commands (slice022). No `/` autocomplete in v1.
 */
interface ComposerProps {
  readonly streaming: boolean;
  readonly disabled: boolean;
  readonly onSend: (text: string) => void;
  readonly onInterrupt: () => void;
}

export function Composer({ streaming, disabled, onSend, onInterrupt }: ComposerProps) {
  const [text, setText] = useState("");
  const taRef = useRef<HTMLTextAreaElement>(null);

  // Auto-grow the textarea up to a cap.
  useEffect(() => {
    const ta = taRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = `${Math.min(ta.scrollHeight, 200)}px`;
  }, [text]);

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    // Shift+Enter → newline (default)
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
      return;
    }
    if (e.key === "Escape") {
      // most-specific first: clear input if it has content
      if (text.length > 0) {
        e.preventDefault();
        setText("");
        return;
      }
      // otherwise: interrupt the streaming turn
      if (streaming) {
        e.preventDefault();
        onInterrupt();
      }
    }
  }

  function submit() {
    const trimmed = text.trim();
    if (!trimmed || disabled) return;
    onSend(trimmed);
    setText("");
  }

  return (
    <div className="cc-composer">
      <textarea
        ref={taRef}
        className="cc-composer__input"
        placeholder="发消息给 CC（Enter 发送，Shift+Enter 换行，Esc 中断/清空）"
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={handleKeyDown}
        disabled={disabled}
        aria-label="CC 消息输入"
      />
      <div className="cc-composer__bar">
        <span className="cc-composer__hint">
          {streaming ? "生成中… Esc 中断" : "就绪"}
        </span>
        {streaming ? (
          <button
            type="button"
            className="cc-composer__btn cc-composer__btn--interrupt"
            onClick={onInterrupt}
          >
            中断
          </button>
        ) : (
          <button
            type="button"
            className="cc-composer__btn cc-composer__btn--send"
            onClick={submit}
            disabled={disabled || text.trim().length === 0}
          >
            发送
          </button>
        )}
      </div>
    </div>
  );
}
