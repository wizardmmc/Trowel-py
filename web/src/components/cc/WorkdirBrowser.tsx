import type { DirEntry } from "../../api/cc";

interface WorkdirBrowserProps {
  readonly input: string;
  readonly parent: string;
  readonly children: readonly DirEntry[];
  readonly isEmpty: boolean;
  readonly recents: readonly string[];
  readonly favorites: readonly string[];
  readonly onAscend: () => void;
  readonly onBrowse: (path: string) => void;
  readonly onSelect: (path: string) => void;
  readonly onPickSaved: (path: string) => void;
}

export function WorkdirBrowser({
  input,
  parent,
  children,
  isEmpty,
  recents,
  favorites,
  onAscend,
  onBrowse,
  onSelect,
  onPickSaved,
}: WorkdirBrowserProps) {
  return (
    <>
      <div className="cc-wd__tree" role="listbox" aria-label="目录列表">
        <button
          type="button"
          className="cc-wd__tree-row cc-wd__tree-row--up"
          onClick={onAscend}
          disabled={!parent || parent === input}
        >
          <span className="cc-wd__tree-name">📁 ..</span>
          <span className="cc-wd__tree-hint">上级</span>
        </button>
        {children.map((child) => (
          <button
            key={child.path}
            type="button"
            className={`cc-wd__tree-row${child.path === input ? " cc-wd__tree-row--sel" : ""}`}
            onClick={() => onBrowse(child.path)}
            onDoubleClick={() => onSelect(child.path)}
          >
            <span className="cc-wd__tree-name">📁 {child.name}</span>
          </button>
        ))}
        {isEmpty && <div className="cc-wd__tree-empty">（无子目录）</div>}
      </div>

      <SavedPaths
        label="最近"
        paths={recents}
        onPick={onPickSaved}
      />
      <SavedPaths
        label="收藏"
        paths={favorites}
        starred
        onPick={onPickSaved}
      />
    </>
  );
}

function SavedPaths({
  label,
  paths,
  starred = false,
  onPick,
}: {
  readonly label: string;
  readonly paths: readonly string[];
  readonly starred?: boolean;
  readonly onPick: (path: string) => void;
}) {
  if (paths.length === 0) return null;
  return (
    <div className="cc-wd__section">
      <div className="cc-wd__label">{label}</div>
      <div className="cc-wd__chips">
        {paths.map((path) => (
          <button
            key={path}
            type="button"
            className={`cc-wd__chip${starred ? " cc-wd__chip--star" : ""}`}
            onClick={() => onPick(path)}
          >
            {path}
          </button>
        ))}
      </div>
    </div>
  );
}
