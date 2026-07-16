/**
 * SlashAutocomplete — the '/' command/skill picker.
 *
 * slice-027 C1 grouped rows by `type` (skills / commands). slice-042 P4
 * regroups by `source` (builtin / bundled / user / project / plugin) and lets
 * each group collapse so the ~200 plugin skills don't drown the daily commands.
 *
 * Pure / presentational: the parent (Composer) owns the input, the flat
 * keyboard index, and which groups are collapsed. This component receives
 * already-grouped items (`groups`), the `searching` flag and `collapsed` set it
 * needs to decide expansion, and only renders + reports clicks/toggles. The
 * shared `slashGroups` module is what keeps the Composer's flat index and this
 * render order identical (a drift would land ArrowDown on an invisible row).
 *
 * Plugin items keep their `mp:skill` full name verbatim (cc needs the full name
 * to disambiguate on trigger, C-4); only the display dims the `mp:` prefix.
 */
import type { SlashItem } from "../../api/cc";
import {
  isGroupExpanded,
  type SlashGroup,
  type SlashSource,
} from "./slashGroups";

interface SlashAutocompleteProps {
  /** Items already filtered + grouped + name-sorted (from groupSlashItems). */
  readonly groups: readonly SlashGroup[];
  /** True when the user has typed a non-empty query → forces every group open. */
  readonly searching: boolean;
  /** Manually collapsed sources (only consulted when not searching). */
  readonly collapsed: ReadonlySet<SlashSource>;
  /** Flat index (expanded-groups order) of the keyboard-highlighted row. */
  readonly selectedIndex: number;
  /** Fired on item click or Enter. Parent sends the right text to CC. */
  readonly onSelect: (item: SlashItem) => void;
  /** Fired when a group header is clicked — parent flips its collapse state. */
  readonly onToggleGroup: (source: SlashSource) => void;
}

/** Split a name at its first ":" into [prefix "mp:", rest "skill"]. Used to
 * dim plugin prefixes ("everything-claude-code:") for readability — cc's own
 * skill/command names don't contain ":" so only plugin names are affected. */
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
        // Derived per render from the same rule Composer's flatVisible uses —
        // kept here as a direct call (no memo) so there's one source of truth
        // and no second expansion state to drift out of sync.
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
              // Stop the click from moving focus to this button — the keyboard
              // handlers (Arrow/Enter) live on the textarea, so losing focus
              // would silently break navigation until the user clicks back.
              // preventDefault on mousedown keeps focus where it is (the
              // textarea) while still firing onClick. (jsdom's fireEvent.click
              // doesn't reproduce real-browser focus-on-click, so this is
              // guarded by the standard pattern rather than a unit test.)
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
