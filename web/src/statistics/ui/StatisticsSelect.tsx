/** 为 Statistics 适配全局共享的锚定 Popper 下拉框。 */

import { PopperSelect } from "../../components/ui/PopperSelect";

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

/** 保持 Statistics 既有接口，并统一使用产品级 Popper 定位规则。 */
export function StatisticsSelect({
  value,
  ariaLabel,
  options,
  onValueChange,
  triggerClassName = "",
}: StatisticsSelectProps) {
  return (
    <PopperSelect
      value={value}
      ariaLabel={ariaLabel}
      options={options}
      onValueChange={onValueChange}
      triggerClassName={triggerClassName}
      density="compact"
    />
  );
}
