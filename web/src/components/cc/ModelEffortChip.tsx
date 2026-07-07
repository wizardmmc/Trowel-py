import { useRef, useState, type CSSProperties } from "react";
import { createPortal } from "react-dom";

import type { ModelOption } from "../../api/cc";
import { EFFORT_OPTIONS } from "./EffortPicker";

/**
 * slice-034 feat 3 — the model + effort chips on the Composer's bottom bar.
 *
 * Two independent chips; each opens an upward popover listing the options.
 * This replaces the old top-bar "未连接" / model display: the model chip
 * always shows a value — the session's current alias, or the cc default
 * fallback (``is_default``) when meta hasn't come back yet. Effort falls back
 * to ``auto`` (model-default intensity) when unset.
 *
 * The popover is rendered through a React portal to ``document.body`` so it
 * escapes ``.cc-view { overflow: hidden }`` (the float-card clip that would
 * otherwise truncate an upward-absolute popover). Position is `fixed` against
 * the chip button's ``getBoundingClientRect()``. A transparent backdrop closes
 * the popover on outside click.
 */
interface ModelEffortChipProps {
  readonly models: readonly ModelOption[];
  /** Current model alias, or null when meta hasn't arrived (show the default). */
  readonly currentModelAlias: string | null;
  /** Current effort, or null when unset (show ``auto``). */
  readonly currentEffort: string | null;
  readonly onPickModel: (alias: string) => void;
  readonly onPickEffort: (value: string) => void;
}

const EFFORT_FALLBACK = "auto";

export function ModelEffortChip({
  models,
  currentModelAlias,
  currentEffort,
  onPickModel,
  onPickEffort,
}: ModelEffortChipProps) {
  const [open, setOpen] = useState<"model" | "effort" | null>(null);
  const [anchor, setAnchor] = useState<{ top: number; left: number } | null>(null);
  const modelBtnRef = useRef<HTMLButtonElement>(null);
  const effortBtnRef = useRef<HTMLButtonElement>(null);

  const defaultModel = models.find((m) => m.is_default) ?? models[0] ?? null;
  const modelDisplay = currentModelAlias ?? defaultModel?.value ?? "—";
  const effortDisplay = currentEffort ?? EFFORT_FALLBACK;

  function close() {
    setOpen(null);
    setAnchor(null);
  }
  function toggle(which: "model" | "effort") {
    if (open === which) {
      close();
      return;
    }
    const ref = which === "model" ? modelBtnRef.current : effortBtnRef.current;
    if (ref) {
      const r = ref.getBoundingClientRect();
      setAnchor({ top: r.top, left: r.left });
    }
    setOpen(which);
  }
  function popoverStyle(): CSSProperties | undefined {
    if (!anchor) return undefined;
    // fixed: bottom = distance from chip top to viewport bottom + 6px gap.
    return {
      position: "fixed",
      bottom: `${window.innerHeight - anchor.top + 6}px`,
      left: `${anchor.left}px`,
    };
  }

  return (
    <div className="cc-chip-group">
      <div className={`cc-chip${open === "model" ? " cc-chip--open" : ""}`}>
        <button
          type="button"
          ref={modelBtnRef}
          className="cc-chip__btn"
          onClick={() => toggle("model")}
          aria-label={`model: ${modelDisplay}（点击切换）`}
        >
          <span className="cc-chip__label">model</span>
          <span className="cc-chip__value">{modelDisplay}</span>
          <span className="cc-chip__caret" aria-hidden="true">▲</span>
        </button>
        {open === "model" && anchor && createPortal(
          <>
            <div className="cc-picker-backdrop" onClick={close} />
            <div
              className="cc-picker"
              role="listbox"
              aria-label="model 选项"
              style={popoverStyle()}
            >
              {models.map((m) => {
                const sel = m.value === modelDisplay;
                return (
                  <button
                    key={m.value}
                    type="button"
                    role="option"
                    aria-selected={sel}
                    className={`cc-picker__item${sel ? " cc-picker__item--sel" : ""}`}
                    onClick={() => {
                      onPickModel(m.value);
                      close();
                    }}
                  >
                    <span className="cc-picker__row">
                      <span className="cc-picker__name">{m.value}</span>
                      {m.is_default && <span className="cc-picker__tag">默认</span>}
                      <span className="cc-picker__real">{m.real_model}</span>
                    </span>
                    <span className="cc-picker__desc">{m.description}</span>
                  </button>
                );
              })}
              {defaultModel && (
                <div className="cc-picker__foot">
                  不设置时默认用 {defaultModel.value}
                </div>
              )}
            </div>
          </>,
          document.body,
        )}
      </div>

      <div className={`cc-chip${open === "effort" ? " cc-chip--open" : ""}`}>
        <button
          type="button"
          ref={effortBtnRef}
          className="cc-chip__btn"
          onClick={() => toggle("effort")}
          aria-label={`effort: ${effortDisplay}（点击切换）`}
        >
          <span className="cc-chip__label">effort</span>
          <span className="cc-chip__value">{effortDisplay}</span>
          <span className="cc-chip__caret" aria-hidden="true">▲</span>
        </button>
        {open === "effort" && anchor && createPortal(
          <>
            <div className="cc-picker-backdrop" onClick={close} />
            <div
              className="cc-picker"
              role="listbox"
              aria-label="effort 选项"
              style={popoverStyle()}
            >
              {EFFORT_OPTIONS.map((o) => {
                const sel = o.value === effortDisplay;
                return (
                  <button
                    key={o.value}
                    type="button"
                    role="option"
                    aria-selected={sel}
                    className={`cc-picker__item${sel ? " cc-picker__item--sel" : ""}`}
                    onClick={() => {
                      onPickEffort(o.value);
                      close();
                    }}
                  >
                    <span className="cc-picker__row">
                      <span className="cc-picker__name">{o.value}</span>
                      {o.value === EFFORT_FALLBACK && (
                        <span className="cc-picker__tag">默认</span>
                      )}
                    </span>
                    <span className="cc-picker__desc">{o.description}</span>
                  </button>
                );
              })}
              <div className="cc-picker__foot">不设置时默认用 {EFFORT_FALLBACK}</div>
            </div>
          </>,
          document.body,
        )}
      </div>
    </div>
  );
}
