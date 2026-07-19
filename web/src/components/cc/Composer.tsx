import { useEffect, useMemo, useRef, useState } from "react";
import { MemoryProfileChip } from "./MemoryProfileChip";
import { ModelEffortChip } from "./ModelEffortChip";
import { SlashAutocomplete } from "./SlashAutocomplete";
import {
  flatVisible,
  groupSlashItems,
  type SlashSource,
} from "./slashGroups";
import type { ModelOption, SlashItem } from "../../api/cc";

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
  /** slice-034 feat 3: model/effort chips on the bottom bar. Omit = no chips. */
  readonly models?: readonly ModelOption[];
  readonly currentModelAlias?: string | null;
  readonly currentEffort?: string | null;
  readonly onPickModel?: (alias: string) => void;
  readonly onPickEffort?: (value: string) => void;
  /** slice-060: frozen memory/profile condition for the active session. When
   * both are non-null, read-only chips render next to model/effort. */
  readonly memoryEnabled?: boolean | null;
  readonly profileEnabled?: boolean | null;
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
  models,
  currentModelAlias,
  currentEffort,
  onPickModel,
  onPickEffort,
  memoryEnabled,
  profileEnabled,
}: ComposerProps) {
  const [text, setText] = useState("");
  const [acIndex, setAcIndex] = useState(0);
  const [dismissed, setDismissed] = useState(false);
  // slice-042 P4: which source groups the user has collapsed. Plugin starts
  // collapsed so its ~200 skills don't drown the daily commands; any group is
  // toggleable via its header. Owned here (not in SlashAutocomplete) because
  // the keyboard index must skip collapsed rows — the component can't own the
  // flat list the Composer navigates.
  const [collapsedSources, setCollapsedSources] = useState<ReadonlySet<SlashSource>>(
    () => new Set<SlashSource>(["plugin"]),
  );
  const taRef = useRef<HTMLTextAreaElement>(null);

  const acOpen =
    text.startsWith("/") &&
    (slashItems?.length ?? 0) > 0 &&
    !dismissed;
  const query = acOpen ? text.slice(1) : "";
  const searching = query.trim() !== "";

  // Shared with SlashAutocomplete via slashGroups: same filter + group order, so
  // the flat index below lines up with the rows it renders. Recomputed cheaply
  // (a few hundred items); memoized on its inputs.
  const acGroups = useMemo(
    () => groupSlashItems(slashItems ?? [], query),
    [slashItems, query],
  );
  const acFlat = useMemo(
    () => flatVisible(acGroups, searching, collapsedSources),
    [acGroups, searching, collapsedSources],
  );
  // Always keep the highlighted index inside the visible rows. Collapsing a
  // group (or the item set changing under a stale index) can shrink acFlat
  // below acIndex; clamping the DISPLAYED index here means the highlight never
  // points at a row that isn't rendered. (Typing resets acIndex to 0 already;
  // toggleGroup clamps the stored index too so arrow nav stays tidy.)
  const safeIndex = Math.min(acIndex, Math.max(0, acFlat.length - 1));

  // Auto-grow the textarea up to a cap.
  useEffect(() => {
    const ta = taRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = `${Math.min(ta.scrollHeight, 200)}px`;
  }, [text]);

  function toggleGroup(source: SlashSource) {
    const next = new Set(collapsedSources);
    if (next.has(source)) next.delete(source);
    else next.add(source);
    setCollapsedSources(next);
    // Collapsing shrinks the flat list; clamp the stored index into the new
    // range right away so the next Arrow key starts from a valid row.
    const nextLen = flatVisible(acGroups, searching, next).length;
    setAcIndex((i) => Math.min(i, Math.max(0, nextLen - 1)));
  }

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
      // Clamp to the visible (expanded) rows so ArrowDown can't run past the
      // list or onto a collapsed group's hidden rows. acFlat matches the order
      // SlashAutocomplete renders, so the index tracks the highlighted row.
      const last = acFlat.length - 1;
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setAcIndex((i) => Math.min(last, i + 1));
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
        // open their picker via pickItem; skills/commands fill `/<name> `).
        // safeIndex is already clamped to the visible rows (same value this
        // inline min would compute) — read it directly instead of re-clamping.
        const item = acFlat[safeIndex];
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
          groups={acGroups}
          searching={searching}
          collapsed={collapsedSources}
          selectedIndex={safeIndex}
          onSelect={pickItem}
          onToggleGroup={toggleGroup}
        />
      )}
      <div className="cc-composer__shell">
        <textarea
          ref={taRef}
          className="cc-composer__input"
          placeholder={
            awaitingInput
              ? "等你回答上方问题（Enter 发送）"
              : "发消息给 Agent（Enter 发送，Shift+Enter 换行，Esc 中断/清空，/ 触发命令补全）"
          }
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
          {models && models.length > 0 && onPickModel && onPickEffort && (
            <ModelEffortChip
              models={models}
              currentModelAlias={currentModelAlias ?? null}
              currentEffort={currentEffort ?? null}
              onPickModel={onPickModel}
              onPickEffort={onPickEffort}
            />
          )}
          {memoryEnabled != null && profileEnabled != null && (
            <MemoryProfileChip
              memoryEnabled={memoryEnabled}
              profileEnabled={profileEnabled}
            />
          )}
          <span className="cc-composer__spacer" />
          {/* slice-034 feat 2: 圆形箭头按钮（无文字）；streaming 变停止图标 */}
          <button
            type="button"
            className={`cc-composer__send${streaming ? " cc-composer__send--stop" : ""}`}
            onClick={streaming ? onInterrupt : submit}
            disabled={!streaming && (disabled || text.trim().length === 0)}
            aria-label={streaming ? "中断" : "发送"}
            title={streaming ? "中断（Esc）" : "发送（Enter）"}
          >
            {streaming ? (
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <rect x="6" y="6" width="12" height="12" rx="2" />
              </svg>
            ) : (
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="M12 19V5M5 12l7-7 7 7" />
              </svg>
            )}
          </button>
        </div>
      </div>
    </div>
  );
}
