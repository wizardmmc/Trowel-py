import { useEffect, useRef, useState } from "react";
import type { ModelOption } from "../../api/cc";

interface ModelPickerProps {
  readonly models: readonly ModelOption[];
  readonly currentModel: string | null;
  readonly onSelect: (aliasValue: string) => void;
  readonly onCancel: () => void;
}

export function ModelPicker({
  models,
  currentModel,
  onSelect,
  onCancel,
}: ModelPickerProps) {
  const initialActive = (() => {
    const idx = models.findIndex((m) => m.value === currentModel);
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
      setActiveIndex((i) => Math.min(i + 1, models.length - 1));
    } else if (e.key === "ArrowUp" || e.key === "ArrowLeft") {
      e.preventDefault();
      setActiveIndex((i) => Math.max(i - 1, 0));
    } else if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      const m = models[activeIndex];
      if (m) onSelect(m.value);
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
        aria-label="选择模型"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="cc-modal__head">
          <span className="cc-modal__title">选择模型</span>
          <span className="cc-modal__close">⎋ esc</span>
        </div>
        <div
          ref={listboxRef}
          className="cc-modal__list"
          role="listbox"
          aria-label="模型选项"
          tabIndex={0}
          onKeyDown={handleKeyDown}
        >
          {models.map((m, i) => {
            const isCurrent = m.value === currentModel;
            const isActive = i === activeIndex;
            return (
              <div
                key={m.value}
                role="option"
                aria-selected={isActive}
                className={`cc-opt${isActive ? " cc-opt--sel" : ""}`}
                onClick={() => onSelect(m.value)}
                onMouseEnter={() => setActiveIndex(i)}
              >
                <div className="cc-opt__body">
                  <div className="cc-opt__title-row">
                    <span className="cc-opt__name">{m.label}</span>
                    {isCurrent && <span className="cc-opt__cur-badge">当前</span>}
                  </div>
                  <div className="cc-opt__meta">{m.real_model}</div>
                  {m.description && (
                    <div className="cc-opt__desc">{m.description}</div>
                  )}
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
