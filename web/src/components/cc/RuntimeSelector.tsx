import { useEffect, useRef } from "react";

import type { Runtime } from "../../api/agent";
import {
  RUNTIME_OPTIONS,
  runtimeOptionIndex,
} from "./newSessionOptions";

interface RuntimeSelectorProps {
  readonly runtime: Runtime;
  readonly creating: boolean;
  readonly catalogLoading: boolean;
  readonly catalogError: string | null;
  readonly isConnected: (runtime: Runtime) => boolean;
  readonly onSelect: (runtime: Runtime) => void;
  readonly onRetry?: () => void;
}

export function RuntimeSelector({
  runtime,
  creating,
  catalogLoading,
  catalogError,
  isConnected,
  onSelect,
  onRetry,
}: RuntimeSelectorProps) {
  const radioRefs = useRef<Array<HTMLButtonElement | null>>([]);

  // 选中项与焦点必须同步，方向键还需跳过未连接的 runtime。
  useEffect(() => {
    radioRefs.current[runtimeOptionIndex(runtime)]?.focus();
  }, [runtime]);

  function onKeyDown(
    event: React.KeyboardEvent<HTMLButtonElement>,
    index: number,
  ): void {
    if (
      event.key !== "ArrowRight" &&
      event.key !== "ArrowDown" &&
      event.key !== "ArrowLeft" &&
      event.key !== "ArrowUp"
    ) {
      return;
    }
    event.preventDefault();
    const direction =
      event.key === "ArrowRight" || event.key === "ArrowDown" ? 1 : -1;
    for (let step = 1; step <= RUNTIME_OPTIONS.length; step++) {
      const nextIndex =
        (((index + direction * step) % RUNTIME_OPTIONS.length) +
          RUNTIME_OPTIONS.length) %
        RUNTIME_OPTIONS.length;
      const next = RUNTIME_OPTIONS[nextIndex];
      if (isConnected(next.value)) {
        onSelect(next.value);
        return;
      }
    }
  }

  return (
    <>
      <div className="cc-dialog__section-label">
        Runtime（创建后不可切换）
      </div>
      <div
        className="cc-dialog__runtime-grid"
        role="radiogroup"
        aria-label="Runtime"
      >
        {RUNTIME_OPTIONS.map((option, index) => {
          const connected = isConnected(option.value);
          const selected = runtime === option.value;
          return (
            <button
              key={option.value}
              ref={(element) => {
                radioRefs.current[index] = element;
              }}
              type="button"
              role="radio"
              aria-checked={selected}
              tabIndex={selected ? 0 : -1}
              disabled={creating}
              aria-disabled={!connected || undefined}
              className={
                "cc-dialog__runtime-card" +
                (selected ? " cc-dialog__runtime-card--selected" : "") +
                (!connected ? " cc-dialog__runtime-card--disabled" : "")
              }
              onClick={() => {
                if (connected) onSelect(option.value);
              }}
              onKeyDown={(event) => onKeyDown(event, index)}
            >
              <span className="cc-dialog__runtime-name">{option.label}</span>
              <span className="cc-dialog__runtime-native">
                {option.native}
              </span>
              <span className="cc-dialog__runtime-desc">{option.desc}</span>
              {!connected && (
                <span className="cc-dialog__runtime-unavailable">未连接</span>
              )}
            </button>
          );
        })}
      </div>

      {catalogLoading && (
        <div className="cc-dialog__diag">正在检查 runtime 连接…</div>
      )}
      {catalogError !== null && (
        <div className="cc-dialog__diag" role="alert">
          Agent API 不可用：{catalogError}
          {onRetry && (
            <button
              type="button"
              className="cc-dialog__btn"
              onClick={onRetry}
              disabled={creating}
              style={{ marginLeft: 8 }}
            >
              重试
            </button>
          )}
        </div>
      )}
    </>
  );
}
