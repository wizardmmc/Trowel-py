/**
 * slashGroups — pure grouping/filtering for the '/' picker (slice-042 P4).
 *
 * Why this module exists: the keyboard index the Composer navigates (ArrowUp/
 * Down/Enter) is a FLAT index into the visible rows, and SlashAutocomplete
 * renders those same rows in the same order. Both must agree on (a) how items
 * filter + group and (b) which groups are collapsed. Centralizing that here
 * keeps the Composer's flat list and the component's render perfectly aligned —
 * a drift would send ArrowDown onto an invisible (collapsed) row.
 *
 * slice-027 grouped by `type` (skills/commands); slice-042 regroups by
 * `source` (builtin/bundled/user/project/plugin) and lets the plugin group
 * collapse so its ~200 skills don't drown the daily commands.
 */
import type { SlashItem } from "../../api/cc";

/** Every source the backend emits. Keep in sync with SlashItem.source. */
export type SlashSource = "builtin" | "bundled" | "user" | "project" | "plugin";

/**
 * Display order. Rationale: tcc's own commands (model/effort/cost/status) are
 * the most-used, so they sit on top; then cc's bundled skills; then the user's
 * own skills; then repo-specific project skills; plugins last (collapsed by
 * default — they're the long tail).
 */
export const SLASH_SOURCE_ORDER: readonly SlashSource[] = [
  "builtin",
  "bundled",
  "user",
  "project",
  "plugin",
];

/** One source bucket ready to render. items are name-sorted within the group. */
export interface SlashGroup {
  readonly source: SlashSource;
  readonly items: readonly SlashItem[];
}

/**
 * Filter items by a name substring (case-insensitive; empty/whitespace query =
 * no filter), then bucket by source in SLASH_SOURCE_ORDER, name-sorting within
 * each bucket and dropping empty buckets. Unknown sources (defensive — backend
 * shouldn't emit any) are appended at the end, sorted, so nothing vanishes.
 */
export function groupSlashItems(
  items: readonly SlashItem[],
  query: string,
): readonly SlashGroup[] {
  const q = query.trim().toLowerCase();
  const filtered = q ? items.filter((i) => i.name.toLowerCase().includes(q)) : items;

  const bySource = new Map<SlashSource, SlashItem[]>();
  for (const item of filtered) {
    const bucket = bySource.get(item.source) ?? [];
    bucket.push(item);
    bySource.set(item.source, bucket);
  }

  // Pin the locale so the within-group order is deterministic across machines
  // (Node ICU / browser locale otherwise vary), keeping the Composer flat index
  // and the rendered order stable everywhere.
  const sortByName = (a: SlashItem, b: SlashItem): number =>
    a.name.localeCompare(b.name, "en");

  const groups: SlashGroup[] = [];
  for (const source of SLASH_SOURCE_ORDER) {
    const bucket = bySource.get(source);
    if (!bucket || bucket.length === 0) continue;
    groups.push({ source, items: [...bucket].sort(sortByName) });
    bySource.delete(source);
  }
  // Any source the backend added that isn't in SLASH_SOURCE_ORDER — append so
  // it still shows rather than silently disappearing.
  for (const source of [...bySource.keys()].sort() as SlashSource[]) {
    groups.push({ source, items: [...bySource.get(source)!].sort(sortByName) });
  }
  return groups;
}

/**
 * Is a group's body shown? While searching, yes for all (the filter already
 * pared each group to matches, and hiding a matched group would surprise).
 * When idle, the manual `collapsed` set decides — plugin starts in it.
 */
export function isGroupExpanded(
  source: SlashSource,
  searching: boolean,
  collapsed: ReadonlySet<SlashSource>,
): boolean {
  if (searching) return true;
  return !collapsed.has(source);
}

/**
 * The flat, keyboard-navigable list: items of expanded groups, in render order.
 * Composer indexes ArrowUp/Down/Enter into this; SlashAutocomplete must render
 * expanded rows in this same order so the highlighted row tracks the index.
 */
export function flatVisible(
  groups: readonly SlashGroup[],
  searching: boolean,
  collapsed: ReadonlySet<SlashSource>,
): readonly SlashItem[] {
  const out: SlashItem[] = [];
  for (const g of groups) {
    if (isGroupExpanded(g.source, searching, collapsed)) {
      out.push(...g.items);
    }
  }
  return out;
}

