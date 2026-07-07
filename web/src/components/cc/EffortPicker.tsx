/**
 * EffortPicker — modal effort picker for /effort (slice-027 C2).
 *
 * listbox semantics (same as ModelPicker): ArrowUp/Down moves the active row,
 * Enter/Space confirms, Esc cancels. cc's native /effort doesn't pop a picker
 * (it just prints the current value); trowel enhances it to a picker over the
 * 6 fixed levels. ultracode is cc 2.1.197+ (xhigh + auto multi-agent); flagged
 * "GLM unverified" so the user knows it may auto-downgrade on the backend.
 */
import { useEffect, useRef, useState } from "react";

interface EffortPickerProps {
  /** Currently in-effect effort (from createSession params / ModelChangedEvent). */
  readonly currentEffort: string | null;
  readonly onSelect: (value: string) => void;
  readonly onCancel: () => void;
}

interface EffortOption {
  readonly value: string;
  readonly description: string;
  readonly tag?: string;
}

export const EFFORT_OPTIONS: readonly EffortOption[] = [
  { value: "low", description: "快速直接，简单改动" },
  { value: "medium", description: "平衡，标准测试覆盖" },
  { value: "high", description: "深入实现，详尽测试" },
  {
    value: "max",
    description: "最强推理（cc：Opus 专属，其它自动降级 high）",
  },
  { value: "auto", description: "用模型默认强度" },
  {
    value: "ultracode",
    description: "xhigh + 自动多 agent 编排（cc 2.1.197+）",
    tag: "GLM 后端 xhigh 支持性待实测 · cc 自动降级兜底",
  },
];

export function EffortPicker({
  currentEffort,
  onSelect,
  onCancel,
}: EffortPickerProps) {
  const initialActive = (() => {
    const idx = EFFORT_OPTIONS.findIndex((o) => o.value === currentEffort);
    return idx >= 0 ? idx : 0;
  })();
  const [activeIndex, setActiveIndex] = useState(initialActive);
  const listboxRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    listboxRef.current?.focus();
  }, []);

  function handleKeyDown(e: React.KeyboardEvent<HTMLDivElement>) {
    if (e.key === "ArrowDown" || e.key === "ArrowRight") {
      e.preventDefault();
      setActiveIndex((i) => Math.min(i + 1, EFFORT_OPTIONS.length - 1));
    } else if (e.key === "ArrowUp" || e.key === "ArrowLeft") {
      e.preventDefault();
      setActiveIndex((i) => Math.max(i - 1, 0));
    } else if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      const o = EFFORT_OPTIONS[activeIndex];
      if (o) onSelect(o.value);
    } else if (e.key === "Escape") {
      e.preventDefault();
      onCancel();
    }
  }

  return (
    <div className="cc-modal-backdrop" onClick={onCancel}>
      <div
        className="cc-modal"
        role="dialog"
        aria-label="选择 effort"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="cc-modal__head">
          <span className="cc-modal__title">选择 effort（思考强度）</span>
          <span className="cc-modal__close">⎋ esc</span>
        </div>
        <div
          ref={listboxRef}
          className="cc-modal__list"
          role="listbox"
          aria-label="effort 选项"
          tabIndex={0}
          onKeyDown={handleKeyDown}
        >
          {EFFORT_OPTIONS.map((o, i) => {
            const isCurrent = o.value === currentEffort;
            const isActive = i === activeIndex;
            return (
              <div
                key={o.value}
                role="option"
                aria-selected={isActive}
                className={`cc-opt${isActive ? " cc-opt--sel" : ""}`}
                onClick={() => onSelect(o.value)}
                onMouseEnter={() => setActiveIndex(i)}
              >
                <div className="cc-opt__body">
                  <div className="cc-opt__title-row">
                    <span className="cc-opt__name">{o.value}</span>
                    {isCurrent && (
                      <span className="cc-opt__cur-badge">当前</span>
                    )}
                  </div>
                  <div className="cc-opt__desc">{o.description}</div>
                  {o.tag && <span className="cc-opt__tag">{o.tag}</span>}
                </div>
              </div>
            );
          })}
        </div>
        <div className="cc-modal__foot">
          <button type="button" className="cc-btn" onClick={onCancel}>
            取消
          </button>
          <span className="cc-modal__hint">↑↓ 选择 · Enter 确认 · 下条消息生效</span>
        </div>
      </div>
    </div>
  );
}
