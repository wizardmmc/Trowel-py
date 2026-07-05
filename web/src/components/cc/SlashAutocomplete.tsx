/**
 * SlashAutocomplete — the '/' command/skill picker (slice-027 C1).
 *
 * Renders above the composer when the user typed `/<query>`. Lists matching
 * SlashItems grouped by type (skills first, then commands) with name +
 * description + source badge, exactly like cc's own commandSuggestions.ts
 * (the data with descriptions cc init doesn't carry).
 *
 * Pure / presentational: the parent (Composer) owns the input, the selected
 * index, and keyboard handling (Arrow/Enter/Esc). This component only filters,
 * groups, highlights, and reports clicks. Empty result → renders nothing.
 */
import { useMemo } from "react";
import type { SlashItem } from "../../api/cc";

interface SlashAutocompleteProps {
  /** The text after `/` the user has typed (e.g. "mon"). Empty = show all. */
  readonly query: string;
  /** All slash items for the current workdir (from GET /cc/slash-items). */
  readonly items: readonly SlashItem[];
  /** Flat index (skills-then-commands) of the keyboard-highlighted row. */
  readonly selectedIndex: number;
  /** Fired on click or Enter. Parent sends the right text to CC. */
  readonly onSelect: (item: SlashItem) => void;
}

export function SlashAutocomplete({
  query,
  items,
  selectedIndex,
  onSelect,
}: SlashAutocompleteProps) {
  const groups = useMemo(() => {
    const q = query.trim().toLowerCase();
    // substring match (covers both prefix "mon"→monthly and infix "view"→review)
    const filtered = q
      ? items.filter((i) => i.name.toLowerCase().includes(q))
      : items;
    const skills = filtered.filter((i) => i.type === "skill");
    const commands = filtered.filter((i) => i.type === "command");
    return [
      { label: `skills · ${skills.length}`, items: skills },
      { label: `commands · ${commands.length}`, items: commands },
    ].filter((g) => g.items.length > 0);
  }, [query, items]);

  const totalCount = groups.reduce((n, g) => n + g.items.length, 0);
  if (totalCount === 0) return null;

  let runningIndex = 0;
  return (
    <div className="cc-ac" role="listbox" aria-label="slash 命令补全">
      {groups.map((g) => (
        <div key={g.label} className="cc-ac__group" role="group" aria-label={g.label}>
          <div className="cc-ac__group-label">{g.label}</div>
          {g.items.map((item) => {
            const idx = runningIndex++;
            const selected = idx === selectedIndex;
            return (
              <div
                key={`${item.type}:${item.name}`}
                role="option"
                aria-selected={selected}
                className={`cc-ac__item${selected ? " cc-ac__item--sel" : ""}`}
                onClick={() => onSelect(item)}
              >
                <span className="cc-ac__name">/{item.name}</span>
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
      ))}
    </div>
  );
}
