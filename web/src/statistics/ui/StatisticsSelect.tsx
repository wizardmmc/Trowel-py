/** 提供锚定在触发器下方的 Statistics 自定义下拉选择框。 */

import * as SelectPrimitive from "@radix-ui/react-select";
import "./statistics-select.css";

export interface StatisticsSelectOption {
  /** 交给筛选状态和接口的稳定值。 */
  readonly value: string;
  /** 展示给用户的选项名称。 */
  readonly label: string;
}

export interface StatisticsSelectProps {
  /** 当前选中的稳定值。 */
  readonly value: string;
  /** 触发器和列表框共用的可访问名称。 */
  readonly ariaLabel: string;
  /** 可选值及其展示名称。 */
  readonly options: readonly StatisticsSelectOption[];
  /** 用户选择新值后通知状态 owner。 */
  readonly onValueChange: (value: string) => void;
  /** 追加到触发器的领域样式类。 */
  readonly triggerClassName?: string;
}

/**
 * 用 Radix Popper 替代系统原生 Select 菜单。
 *
 * 菜单通过 Portal 脱离统计卡片，并从触发器下沿留出 6px 后开始，当前选中项不会再
 * 与触发框重叠。碰撞检测仅在视口确实放不下时调整位置。
 */
export function StatisticsSelect({
  value,
  ariaLabel,
  options,
  onValueChange,
  triggerClassName = "",
}: StatisticsSelectProps) {
  return (
    <SelectPrimitive.Root value={value} onValueChange={onValueChange}>
      <SelectPrimitive.Trigger
        className={`statistics-select__trigger ${triggerClassName}`.trim()}
        aria-label={ariaLabel}
        title={options.find((option) => option.value === value)?.label}
      >
        <SelectPrimitive.Value />
        <SelectPrimitive.Icon className="statistics-select__chevron">
          <ChevronIcon />
        </SelectPrimitive.Icon>
      </SelectPrimitive.Trigger>
      <SelectPrimitive.Portal>
        <SelectPrimitive.Content
          className="statistics-select__content"
          position="popper"
          side="bottom"
          sideOffset={6}
          align="start"
          collisionPadding={16}
        >
          <SelectPrimitive.ScrollUpButton className="statistics-select__scroll-button">
            <span aria-hidden="true">⌃</span>
          </SelectPrimitive.ScrollUpButton>
          <SelectPrimitive.Viewport className="statistics-select__viewport">
            {options.map((option) => (
              <SelectPrimitive.Item
                key={option.value}
                value={option.value}
                className="statistics-select__item"
                title={option.label}
              >
                <SelectPrimitive.ItemIndicator className="statistics-select__indicator">
                  <CheckIcon />
                </SelectPrimitive.ItemIndicator>
                <SelectPrimitive.ItemText>{option.label}</SelectPrimitive.ItemText>
              </SelectPrimitive.Item>
            ))}
          </SelectPrimitive.Viewport>
          <SelectPrimitive.ScrollDownButton className="statistics-select__scroll-button">
            <span aria-hidden="true">⌄</span>
          </SelectPrimitive.ScrollDownButton>
        </SelectPrimitive.Content>
      </SelectPrimitive.Portal>
    </SelectPrimitive.Root>
  );
}

/** 绘制触发器右侧的展开箭头。 */
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
