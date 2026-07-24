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
  readonly onToggleGroup: (source: SlashSource) => void;
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
  onToggleGroup,
}: SlashAutocompleteProps) {
  let runningIndex = 0;
  if (groups.length === 0) return null;
  return (
    <div className="cc-ac" role="listbox" aria-label="slash 命令补全">
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
              {g.source} · {g.items.length}
            </button>
            {open &&
              g.items.map((item) => {
                const idx = runningIndex++;
                const selected = idx === selectedIndex;
                const { prefix, rest } = splitNameAtColon(item.name);
                return (
                  <div
                    key={`${item.source}:${item.name}`}
                    role="option"
                    aria-selected={selected}
                    className={`cc-ac__item${selected ? " cc-ac__item--sel" : ""}`}
                    onClick={() => onSelect(item)}
                  >
                    <span className="cc-ac__name">
                      /{prefix && <span className="cc-ac__pre">{prefix}</span>}
                      {rest}
                    </span>
                    <span className={`cc-ac__badge cc-ac__badge--${item.source}`}>
                      {item.source}
                    </span>
                    {item.description && (
                      <div className="cc-ac__desc">{item.description}</div>
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
