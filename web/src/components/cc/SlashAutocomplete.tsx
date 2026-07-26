import { useEffect, useRef } from "react";
import type { SlashItem } from "../../api/cc";
import {
  isGroupExpanded,
  type SlashGroup,
  type SlashSource,
} from "./slashGroups";

interface SlashAutocompleteProps {
  readonly groups: readonly SlashGroup[];
  readonly searching: boolean;
  readonly collapsed: ReadonlySet<SlashSource>;
  readonly selectedIndex: number;
  readonly onSelect: (item: SlashItem) => void;
  readonly onHighlight?: (index: number) => void;
  readonly onToggleGroup: (source: SlashSource) => void;
  readonly id?: string;
  readonly loading?: boolean;
  readonly error?: string | null;
  readonly onRetry?: () => void;
}

function splitNameAtColon(name: string): { prefix: string; rest: string } {
  const i = name.indexOf(":");
  if (i === -1) return { prefix: "", rest: name };
  return { prefix: name.slice(0, i + 1), rest: name.slice(i + 1) };
}

export function SlashAutocomplete({
  groups,
  searching,
  collapsed,
  selectedIndex,
  onSelect,
  onHighlight,
  onToggleGroup,
  id,
  loading = false,
  error = null,
  onRetry,
}: SlashAutocompleteProps) {
  const listRef = useRef<HTMLDivElement>(null);
  let runningIndex = 0;
  useEffect(() => {
    const selected = listRef.current?.querySelector<HTMLElement>(
      '[role="option"][aria-selected="true"]',
    );
    selected?.scrollIntoView?.({ block: "nearest" });
  }, [selectedIndex]);
  if (groups.length === 0 && !loading && !error) return null;
  return (
    <div
      ref={listRef}
      id={id}
      className="cc-ac"
      role="listbox"
      aria-label="slash 命令补全"
      aria-busy={loading}
    >
      {loading && <div className="cc-ac__state" role="status">正在加载 Codex 命令…</div>}
      {error && (
        <div className="cc-ac__state cc-ac__state--error" role="alert">
          <span>命令加载失败</span>
          {onRetry && <button type="button" onClick={onRetry}>重试</button>}
        </div>
      )}
      {groups.map((g) => {
        const open = isGroupExpanded(g.source, searching, collapsed);
        return (
          <div
            key={g.source}
            className="cc-ac__group"
            role="group"
            aria-label={g.source}
          >
            <button
              type="button"
              className="cc-ac__group-label"
              aria-expanded={open}
              aria-label={`${g.source} 组（共 ${g.items.length} 项）${open ? "折叠" : "展开"}`}
              onClick={() => onToggleGroup(g.source)}
              onMouseDown={(e) => e.preventDefault()}
            >
              <span className="cc-ac__tri" aria-hidden="true">{open ? "▾" : "▸"}</span>
              {g.source === "codex" ? "Codex" : g.source} · {g.items.length}
            </button>
            {open &&
              g.items.map((item) => {
                const idx = runningIndex++;
                const selected = idx === selectedIndex;
                const { prefix, rest } = splitNameAtColon(item.name);
                return (
                  <div
                    key={`${item.source}:${item.name}`}
                    id={id ? `${id}-option-${idx}` : undefined}
                    role="option"
                    aria-selected={selected}
                    aria-disabled={item.disabled ? "true" : undefined}
                    className={`cc-ac__item${selected ? " cc-ac__item--sel" : ""}${item.disabled ? " cc-ac__item--disabled" : ""}`}
                    onMouseDown={(event) => event.preventDefault()}
                    onMouseEnter={() => {
                      if (!item.disabled) onHighlight?.(idx);
                    }}
                    onClick={() => {
                      if (!item.disabled) onSelect(item);
                    }}
                  >
                    <span className="cc-ac__name">
                      /{prefix && <span className="cc-ac__pre">{prefix}</span>}
                      {rest}
                    </span>
                    <span className={`cc-ac__badge cc-ac__badge--${item.source}`}>
                      {item.source === "codex" ? "native" : item.source}
                    </span>
                    {item.description && (
                      <div className="cc-ac__copy">
                        <div className="cc-ac__desc">{item.description}</div>
                        {item.disabledReason && (
                          <div className="cc-ac__reason">{item.disabledReason}</div>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
          </div>
        );
      })}
    </div>
  );
}
