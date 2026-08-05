/** 渲染设置页统一的可访问开关。 */

interface SettingsSwitchProps {
  readonly label: string;
  readonly checked: boolean;
  readonly disabled?: boolean;
  readonly onCheckedChange: (checked: boolean) => void;
}

/** 使用 button switch 语义，避免隐藏 checkbox 带来的点击区域歧义。 */
export function SettingsSwitch({
  label,
  checked,
  disabled = false,
  onCheckedChange,
}: SettingsSwitchProps) {
  return (
    <button
      type="button"
      className="settings-switch"
      role="switch"
      aria-label={label}
      aria-checked={checked}
      disabled={disabled}
      onClick={() => onCheckedChange(!checked)}
    />
  );
}
