import { useEffect, useMemo, useRef, useState } from "react";
import type { DirEntry } from "../../api/cc";
import { listDir } from "../../api/cc";
import { WorkdirBrowser } from "./WorkdirBrowser";

interface WorkdirPickerProps {
  readonly initialPath?: string;
  readonly recents: readonly string[];
  readonly favorites?: readonly string[];
  readonly onSelect: (path: string) => void;
  readonly onCancel: () => void;
}

type Mode = "idle" | "listing";

function splitPath(p: string): { parent: string; last: string } {
  const trimmed = p.replace(/\/+$/, "");
  const i = trimmed.lastIndexOf("/");
  if (i < 0) return { parent: "", last: trimmed };
  return { parent: trimmed.slice(0, i) || "/", last: trimmed.slice(i + 1) };
}

function joinBase(base: string, seg: string): string {
  const b = base ? base.replace(/\/+$/, "") : "";
  return b ? `${b}/${seg}` : `/${seg}`;
}

// 补全目录后保留斜杠，用户可继续输入下一层。
function withSlash(p: string): string {
  return p.endsWith("/") ? p : `${p}/`;
}

// 提交时只清理尾斜杠；~ 仍由服务端展开。
function normSubmit(p: string): string {
  return p.replace(/\/+$/, "");
}

export function WorkdirPicker({
  initialPath = "~",
  recents,
  favorites = [],
  onSelect,
  onCancel,
}: WorkdirPickerProps) {
  const [input, setInput] = useState(initialPath);
  const [siblings, setSiblings] = useState<readonly DirEntry[]>([]);
  const [browseChildren, setBrowseChildren] = useState<readonly DirEntry[]>([]);
  const [isEmpty, setIsEmpty] = useState(false);
  const [mode, setMode] = useState<Mode>("idle");
  const [highlight, setHighlight] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  const { parent, last } = splitPath(input || "~");

  useEffect(() => {
    let cancelled = false;
    listDir(parent || "/")
      .then((c) => {
        if (!cancelled) setSiblings(c);
      })
      .catch(() => {
        if (!cancelled) setSiblings([]);
      });
    return () => {
      cancelled = true;
    };
  }, [parent]);

  useEffect(() => {
    let cancelled = false;
    listDir(input || "/")
      .then((c) => {
        if (cancelled) return;
        setBrowseChildren(c);
        setIsEmpty(c.length === 0);
      })
      .catch(() => {
        if (cancelled) return;
        setBrowseChildren([]);
        setIsEmpty(false);
      });
    return () => {
      cancelled = true;
    };
  }, [input]);

  const candidates = useMemo(() => {
    if (!last) return [];
    return siblings.filter((s) => s.name.startsWith(last) && s.name !== last);
  }, [siblings, last]);

  const commonPrefix = useMemo(() => {
    if (candidates.length === 0) return "";
    const names = candidates.map((c) => c.name);
    let prefix = names[0];
    for (const n of names) {
      while (prefix && !n.startsWith(prefix)) prefix = prefix.slice(0, -1);
    }
    return prefix;
  }, [candidates]);

  const ghost = candidates.length > 0 ? candidates[0].path : "";
  const ghostRest = ghost && ghost.startsWith(input) ? ghost.slice(input.length) : "";

  const trimmed = input.trim();

  function isCursorAtEnd(): boolean {
    const el = inputRef.current;
    return el ? el.selectionStart === el.value.length : true;
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    const n = candidates.length;

    if (e.key === "Tab") {
      e.preventDefault();
      if (n === 0) return;
      if (n === 1) {
        setInput(withSlash(candidates[0].path));
        setMode("idle");
        return;
      }
      if (mode === "idle") {
        // 首次 Tab 遇到多个候选时先补公共前缀，再打开列表。
        if (commonPrefix.length > last.length) {
          setInput(joinBase(parent, commonPrefix));
        }
        setMode("listing");
        setHighlight(0);
      } else {
        setHighlight((h) => (h + 1) % n);
      }
      return;
    }

    if (e.key === "ArrowDown") {
      if (n === 0) return;
      e.preventDefault();
      if (mode === "idle") {
        setMode("listing");
        setHighlight(0);
      } else {
        setHighlight((h) => (h + 1) % n);
      }
      return;
    }
    if (e.key === "ArrowUp") {
      if (mode !== "listing") return;
      e.preventDefault();
      setHighlight((h) => (h - 1 + n) % n);
      return;
    }

    if (e.key === "ArrowRight" && !e.altKey) {
      // 仅在光标位于末尾时接受整段 ghost。
      if (ghostRest && mode === "idle" && isCursorAtEnd()) {
        e.preventDefault();
        setInput(withSlash(ghost));
      }
      return;
    }

    if (e.key === "Enter") {
      if (mode === "listing" && candidates[highlight]) {
        e.preventDefault();
        setInput(withSlash(candidates[highlight].path));
        setMode("idle");
        return;
      }
      e.preventDefault();
      if (trimmed) onSelect(normSubmit(trimmed));
      return;
    }

    if (e.key === "Escape") {
      if (mode === "listing") {
        e.preventDefault();
        setMode("idle");
      } else {
        onCancel();
      }
      return;
    }
  }

  function handleChange(e: React.ChangeEvent<HTMLInputElement>) {
    setInput(e.target.value);
    setMode("idle");
    setHighlight(0);
  }

  return (
    <div className="cc-modal-backdrop" onClick={onCancel}>
      <div
        className="cc-modal cc-modal--wide"
        role="dialog"
        aria-label="选择工作目录"
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="cc-modal__head">
          <span className="cc-modal__title">选择工作目录</span>
          <span className="cc-modal__close">⎋ esc</span>
        </div>
        <div className="cc-modal__body">
          <div className="cc-wd__input-wrap">
            {ghostRest && mode === "idle" && (
              <span className="cc-wd__ghost" aria-hidden="true">
                <span className="cc-wd__ghost-typed">{input}</span>
                <span className="cc-wd__ghost-rest">{ghostRest}</span>
              </span>
            )}
            <input
              ref={inputRef}
              className="cc-wd__input"
              aria-label="工作目录"
              placeholder="输入路径，如 ~/projects/my-app（~ 自动展开）"
              value={input}
              onChange={handleChange}
              onKeyDown={handleKeyDown}
              autoFocus
              spellCheck={false}
              autoComplete="off"
            />
            {mode === "listing" && candidates.length > 0 && (
              <ul className="cc-wd__dropdown" role="listbox" aria-label="补全候选">
                {candidates.map((c, i) => (
                  <li
                    key={c.path}
                    role="option"
                    aria-selected={i === highlight}
                    className={`cc-wd__dropdown-item${i === highlight ? " cc-wd__dropdown-item--sel" : ""}`}
                    onMouseDown={(e) => {
                      // 保持输入框焦点，后续键盘操作仍由输入框接收。
                      e.preventDefault();
                      setHighlight(i);
                    }}
                    onClick={() => {
                      setInput(withSlash(c.path));
                      setMode("idle");
                    }}
                  >
                    <span className="cc-wd__dropdown-name">📁 {c.name}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
          {mode === "idle" && candidates.length > 1 && (
            <div className="cc-wd__hint">
              <kbd>Tab</kbd> 补全前缀 <code>{commonPrefix}/</code>
              {" "}（{candidates.length} 个）
            </div>
          )}

          <WorkdirBrowser
            input={input}
            parent={parent}
            children={browseChildren}
            isEmpty={isEmpty}
            recents={recents}
            favorites={favorites}
            onAscend={() => {
              setInput(parent || "/");
              setMode("idle");
            }}
            onBrowse={(path) => {
              setInput(withSlash(path));
              setMode("idle");
            }}
            onSelect={onSelect}
            onPickSaved={(path) => {
              setInput(path);
              setMode("idle");
            }}
          />
        </div>
        <div className="cc-modal__foot">
          <span className="cc-modal__hint">Tab 补全 · ↑↓ 选择 · → 接受</span>
          <button type="button" className="cc-btn" onClick={onCancel}>
            取消
          </button>
          <button
            type="button"
            className="cc-btn cc-btn--primary"
            onClick={() => trimmed && onSelect(normSubmit(trimmed))}
            disabled={!trimmed}
          >
            确定
          </button>
        </div>
      </div>
    </div>
  );
}
