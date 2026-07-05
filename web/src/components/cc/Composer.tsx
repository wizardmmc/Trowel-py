import { useEffect, useRef, useState } from "react";
import { SlashAutocomplete } from "./SlashAutocomplete";
import type { SlashItem } from "../../api/cc";

/**
 * The message composer. Esc follows the CC-terminal convention with three
 * context-specific behaviors (most-specific first):
 *   1. if `/` autocomplete is open → close it (text kept)
 *   2. if the input has text → clear it
 *   3. if a turn is streaming → interrupt
 * Enter sends, Shift+Enter inserts a newline.
 *
 * slice-027: when `slashItems` is provided and input starts with `/`, a
 * SlashAutocomplete pops up above the textarea. ArrowUp/Down moves the
 * selection, Enter picks (fills `/<name> ` so the user can append args; the
 * backend input layer then expands the skill / custom command). Bare `/model`
 * or `/effort` + Enter opens the corresponding picker instead of sending.
 * Without `slashItems` the composer posts raw text (legacy slice022 path).
 */
interface ComposerProps {
  readonly streaming: boolean;
  readonly disabled: boolean;
  /** True while an AskUserQuestion awaits the user's answer (slice-025-c). */
  readonly awaitingInput?: boolean;
  readonly onSend: (text: string) => void;
  readonly onInterrupt: () => void;
  /** slice-027 C1: slash items for `/` autocomplete. Omit = raw-post legacy. */
  readonly slashItems?: readonly SlashItem[];
  /** slice-027 C2: fired on bare `/model` + Enter (open the model picker). */
  readonly onRequestModelPicker?: () => void;
  /** slice-027 C2: fired on bare `/effort` + Enter. */
  readonly onRequestEffortPicker?: () => void;
}

/** Filter + order items exactly like SlashAutocomplete (skills then commands)
 * so the keyboard index lines up with the highlighted row. */
function flatFiltered(
  items: readonly SlashItem[],
  query: string,
): readonly SlashItem[] {
  const q = query.trim().toLowerCase();
  const f = q ? items.filter((i) => i.name.toLowerCase().includes(q)) : items;
  return [
    ...f.filter((i) => i.type === "skill"),
    ...f.filter((i) => i.type === "command"),
  ];
}

export function Composer({
  streaming,
  disabled,
  awaitingInput,
  onSend,
  onInterrupt,
  slashItems,
  onRequestModelPicker,
  onRequestEffortPicker,
}: ComposerProps) {
  const [text, setText] = useState("");
  const [acIndex, setAcIndex] = useState(0);
  const [dismissed, setDismissed] = useState(false);
  const taRef = useRef<HTMLTextAreaElement>(null);

  const acOpen =
    text.startsWith("/") &&
    (slashItems?.length ?? 0) > 0 &&
    !dismissed;
  const query = acOpen ? text.slice(1) : "";

  // Auto-grow the textarea up to a cap.
  useEffect(() => {
    const ta = taRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = `${Math.min(ta.scrollHeight, 200)}px`;
  }, [text]);

  function pickItem(item: SlashItem) {
    // model/effort open their picker instead of filling the input — reached
    // by Enter OR click, the user wants to choose, not type args.
    if (item.name === "model" && onRequestModelPicker) {
      onRequestModelPicker();
      setText("");
      setDismissed(false);
      return;
    }
    if (item.name === "effort" && onRequestEffortPicker) {
      onRequestEffortPicker();
      setText("");
      setDismissed(false);
      return;
    }
    setText(`/${item.name} `);
    setDismissed(true); // close autocomplete so the user can type args
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (acOpen) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setAcIndex((i) => i + 1);
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setAcIndex((i) => Math.max(0, i - 1));
        return;
      }
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        const trimmed = text.trim();
        // bare /model /effort → picker (regardless of autocomplete contents)
        if (trimmed === "/model" && onRequestModelPicker) {
          onRequestModelPicker();
          setText("");
          setDismissed(false);
          return;
        }
        if (trimmed === "/effort" && onRequestEffortPicker) {
          onRequestEffortPicker();
          setText("");
          setDismissed(false);
          return;
        }
        // bare /cost /status → send immediately (builtin local commands take
        // no args; filling would just force a needless second Enter)
        if (trimmed === "/cost" || trimmed === "/status") {
          onSend(trimmed);
          setText("");
          setDismissed(false);
          return;
        }
        // otherwise pick the highlighted autocomplete row (model/effort rows
        // open their picker via pickItem; skills/commands fill `/<name> `)
        const flat = flatFiltered(slashItems ?? [], query);
        const item = flat[Math.min(acIndex, flat.length - 1)];
        if (item) {
          pickItem(item);
          return;
        }
        // no autocomplete match (e.g. "/monthly-etf args") → fall through to
        // submit so the user's typed text is sent as-is
      }
      if (e.key === "Escape") {
        // most-specific: close autocomplete, keep the text
        e.preventDefault();
        setDismissed(true);
        return;
      }
    }
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
      return;
    }
    if (e.key === "Escape") {
      if (text.length > 0) {
        e.preventDefault();
        setText("");
        setDismissed(false);
        return;
      }
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
    setDismissed(false);
  }

  return (
    <div className="cc-composer">
      {acOpen && (
        <SlashAutocomplete
          query={query}
          items={slashItems ?? []}
          selectedIndex={acIndex}
          onSelect={pickItem}
        />
      )}
      <textarea
        ref={taRef}
        className="cc-composer__input"
        placeholder="发消息给 CC（Enter 发送，Shift+Enter 换行，Esc 中断/清空，/ 触发命令补全）"
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          setDismissed(false);
          setAcIndex(0);
        }}
        onKeyDown={handleKeyDown}
        disabled={disabled}
        aria-label="CC 消息输入"
      />
      <div className="cc-composer__bar">
        <span className="cc-composer__hint">
          {streaming
            ? "生成中… Esc 中断"
            : awaitingInput
              ? "等你回答上方问题"
              : "就绪"}
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
