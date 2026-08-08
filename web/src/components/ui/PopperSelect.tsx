/** 提供固定锚定在触发框下方的共享下拉选择框。 */

import * as SelectPrimitive from "@radix-ui/react-select";
import "./popper-select.css";

export interface PopperSelectOption {
  readonly value: string;
  readonly label: string;
  readonly disabled?: boolean;
}

export interface PopperSelectProps {
  readonly value: string;
  readonly ariaLabel: string;
  readonly options: readonly PopperSelectOption[];
  readonly onValueChange: (value: string) => void;
  readonly triggerClassName?: string;
  readonly disabled?: boolean;
  readonly placeholder?: string;
  readonly density?: "default" | "compact";
  readonly side?: "top" | "bottom";
}

/**
 * 菜单默认从触发框下沿留出 6px 后展开，贴近窗口底部的调用方可显式改为向上。
 *
 * 关闭 Radix 的自动翻转碰撞策略，展开方向始终由调用方决定；可用高度由滚动视口
 * 收口。触发框与菜单处于两棵 DOM 子树，当前选中项不会参与定位。
 */
export function PopperSelect({
  value,
  ariaLabel,
  options,
  onValueChange,
  triggerClassName = "",
  disabled = false,
  placeholder,
  density = "default",
  side = "bottom",
}: PopperSelectProps) {
  const densityClass = density === "compact" ? " is-compact" : "";
  return (
    <SelectPrimitive.Root
      value={value}
      onValueChange={onValueChange}
      disabled={disabled}
    >
      <SelectPrimitive.Trigger
        className={`popper-select__trigger${densityClass} ${triggerClassName}`.trim()}
        aria-label={ariaLabel}
        title={options.find((option) => option.value === value)?.label}
      >
        <SelectPrimitive.Value placeholder={placeholder} />
        <SelectPrimitive.Icon className="popper-select__chevron">
          <ChevronIcon />
        </SelectPrimitive.Icon>
      </SelectPrimitive.Trigger>
      <SelectPrimitive.Portal>
        <SelectPrimitive.Content
          className={`popper-select__content${densityClass}`}
          position="popper"
          side={side}
          sideOffset={6}
          align="start"
          avoidCollisions={false}
        >
          <SelectPrimitive.ScrollUpButton className="popper-select__scroll-button">
            <span aria-hidden="true">⌃</span>
          </SelectPrimitive.ScrollUpButton>
          <SelectPrimitive.Viewport
            className={`popper-select__viewport${densityClass}`}
          >
            {options.map((option) => (
              <SelectPrimitive.Item
                key={option.value}
                value={option.value}
                disabled={option.disabled}
                className={`popper-select__item${densityClass}`}
                title={option.label}
              >
                <SelectPrimitive.ItemIndicator className="popper-select__indicator">
                  <CheckIcon />
                </SelectPrimitive.ItemIndicator>
                <SelectPrimitive.ItemText>{option.label}</SelectPrimitive.ItemText>
              </SelectPrimitive.Item>
            ))}
          </SelectPrimitive.Viewport>
          <SelectPrimitive.ScrollDownButton className="popper-select__scroll-button">
            <span aria-hidden="true">⌄</span>
          </SelectPrimitive.ScrollDownButton>
        </SelectPrimitive.Content>
      </SelectPrimitive.Portal>
    </SelectPrimitive.Root>
  );
}

/** 绘制触发框右侧的展开箭头。 */
function ChevronIcon() {
  return (
    <svg viewBox="0 0 16 16" aria-hidden="true">
      <path d="m4 6 4 4 4-4" />
    </svg>
  );
}

/** 绘制当前选项左侧的选中标记。 */
function CheckIcon() {
  return (
    <svg viewBox="0 0 16 16" aria-hidden="true">
      <path d="m3.5 8 3 3 6-6" />
    </svg>
  );
}
