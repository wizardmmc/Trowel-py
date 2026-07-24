import type { SlashItem } from "../../api/cc";

export type SlashSource = "builtin" | "bundled" | "user" | "project" | "plugin";

export const SLASH_SOURCE_ORDER: readonly SlashSource[] = [
  "builtin",
  "bundled",
  "user",
  "project",
  "plugin",
];

export interface SlashGroup {
  readonly source: SlashSource;
  readonly items: readonly SlashItem[];
}

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

  const sortByName = (a: SlashItem, b: SlashItem): number =>
    a.name.localeCompare(b.name, "en");

  const groups: SlashGroup[] = [];
  for (const source of SLASH_SOURCE_ORDER) {
    const bucket = bySource.get(source);
    if (!bucket || bucket.length === 0) continue;
    groups.push({ source, items: [...bucket].sort(sortByName) });
    bySource.delete(source);
  }
  for (const source of [...bySource.keys()].sort() as SlashSource[]) {
    groups.push({ source, items: [...bySource.get(source)!].sort(sortByName) });
  }
  return groups;
}

export function isGroupExpanded(
  source: SlashSource,
  searching: boolean,
  collapsed: ReadonlySet<SlashSource>,
): boolean {
  if (searching) return true;
  return !collapsed.has(source);
}

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

