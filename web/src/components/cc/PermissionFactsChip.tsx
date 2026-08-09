/** 展示 Codex 请求权限与实际 sandbox、审批和网络能力。 */

import { useEffect, useRef, useState, type CSSProperties } from "react";
import { createPortal } from "react-dom";
import {
  PRESET_LABELS,
  PRESET_ORDER,
  type PermissionPreset,
} from "./permissionPresets";

export type { PermissionPreset } from "./permissionPresets";

interface PermissionFactsChipProps {
  readonly requested: string | null;
  readonly profile: string | null;
  readonly sandbox: string | null;
  readonly approval: string | null;
  readonly network: boolean | null;
  readonly label: string | null;
  /**
   * 可选 preset 选择器；不传 ``onSelectPreset`` 时 popover 只展示 effective facts，
   * 保持旧只读行为。传 ``onSelectPreset`` 后用户可在 popover 内改变下一 turn 的
   * requested permission，下个 turn/start 作为 override 生效。
   */
  readonly selectablePresets?: readonly PermissionPreset[];
  readonly selectedPreset?: PermissionPreset | null;
  readonly onSelectPreset?: (preset: PermissionPreset) => void;
  readonly disabled?: boolean;
}

export function PermissionFactsChip({
  requested,
  profile,
  sandbox,
  approval,
  network,
  label,
  selectablePresets,
  selectedPreset,
  onSelectPreset,
  disabled,
}: PermissionFactsChipProps) {
  const [open, setOpen] = useState(false);
  const [anchor, setAnchor] = useState<{ top: number; left: number } | null>(null);
  // Full access 必须二次确认；pendingDanger 标记用户已选中但未确认的切换。
  const [pendingDanger, setPendingDanger] = useState(false);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const effectiveDanger =
    sandbox === "danger-full-access" && approval === "never";
  const requestedDanger = requested === "danger-full-access";
  const danger = effectiveDanger || requestedDanger;
  // requested 指向 Full access 但 native effective 还没跟上时，aria 后缀提示
  // "待 native 确认"，避免 chip 假装 effective 已到 Full access。
  const pendingNativeConfirm = requestedDanger && !effectiveDanger;
  // chip 主显示优先反映用户请求的 preset；effective label 只在 requested
  // 缺失时兜底，避免 PATCH 后 chip 仍显旧 effective、看上去没改成功。
  const requestedLabel = presetDisplayLabel(requested);
  const baseDisplay = requestedLabel ?? label ?? "follow";
  const display = baseDisplay;
  const accessibleDisplay = pendingNativeConfirm
    ? `${baseDisplay}（待 native 确认）`
    : baseDisplay;
  const presets =
    selectablePresets ?? (onSelectPreset ? PRESET_ORDER : []);
  const canSelect = Boolean(onSelectPreset) && !disabled;
  const ariaAction = canSelect ? "点开修改请求权限" : "查看 effective policy";

  function close(): void {
    setOpen(false);
    setAnchor(null);
    setPendingDanger(false);
  }

  function toggle(): void {
    if (open) {
      close();
      return;
    }
    const rect = buttonRef.current?.getBoundingClientRect();
    if (rect) setAnchor({ top: rect.top, left: rect.left });
    setOpen(true);
  }

  function selectPreset(preset: PermissionPreset): void {
    if (!canSelect) return;
    if (preset === "danger-full-access") {
      // Full access 不立即下发；先弹出确认按钮，避免误触静默放宽权限。
      setPendingDanger(true);
      return;
    }
    setPendingDanger(false);
    onSelectPreset?.(preset);
  }

  function confirmDanger(): void {
    setPendingDanger(false);
    onSelectPreset?.("danger-full-access");
  }

  useEffect(() => {
    if (!open) return;
    function onKeyDown(event: KeyboardEvent): void {
      if (event.key !== "Escape") return;
      close();
      buttonRef.current?.focus();
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open]);

  const popoverStyle: CSSProperties | undefined = anchor
    ? {
        position: "fixed",
        bottom: `${window.innerHeight - anchor.top + 6}px`,
        left: `${anchor.left}px`,
      }
    : undefined;

  return (
    <div className={`cc-chip${open ? " cc-chip--open" : ""}`}>
      <button
        ref={buttonRef}
        type="button"
        className={`cc-chip__btn${danger ? " cc-chip__btn--danger" : ""}`}
        onClick={toggle}
        aria-label={`permission: ${accessibleDisplay}（${ariaAction}）`}
        title={accessibleDisplay}
      >
        <span className="cc-chip__label">permission</span>
        <span className="cc-chip__value">{display}</span>
        <span className="cc-chip__caret" aria-hidden="true">▲</span>
      </button>
      {open && anchor &&
        createPortal(
          <>
            <div className="cc-picker-backdrop" onClick={close} />
            <div
              className={`cc-picker cc-permission-facts${danger ? " cc-permission-facts--danger" : ""}`}
              role="dialog"
              aria-label="Codex effective permission"
              style={popoverStyle}
            >
              {danger && (
                <div className="cc-permission-facts__warning" role="alert">
                  {effectiveDanger
                    ? "Full access：native 已确认无 sandbox，且不会请求审批。"
                    : "已请求 Full access；native thread 启动后会在下方显示实际 sandbox 与 approval。"}
                </div>
              )}
              {canSelect && presets.length > 0 && (
                <div
                  className="cc-permission-facts__section"
                  role="group"
                  aria-label="请求权限（下个 turn 生效）"
                >
                  <div className="cc-permission-facts__section-title">
                    请求权限（下个 turn 生效）
                  </div>
                  {presets.map((preset) => (
                    <button
                      key={preset}
                      type="button"
                      className={`cc-permission-facts__option${
                        preset === selectedPreset
                          ? " cc-permission-facts__option--sel"
                          : ""
                      }`}
                      onClick={() => selectPreset(preset)}
                      disabled={disabled}
                    >
                      <span className="cc-permission-facts__option-name">
                        {PRESET_LABELS[preset]}
                      </span>
                    </button>
                  ))}
                  {pendingDanger && (
                    <button
                      type="button"
                      className="cc-permission-facts__confirm"
                      onClick={confirmDanger}
                    >
                      确认切换到 Full access
                    </button>
                  )}
                </div>
              )}
              <div
                className="cc-permission-facts__section"
                role="group"
                aria-label="原生 effective facts"
              >
                <div className="cc-permission-facts__section-title">
                  原生 effective facts
                </div>
                <Fact label="profile" value={profile} />
                <Fact label="sandbox" value={sandbox} />
                <Fact label="approval" value={approval} />
                <Fact
                  label="network"
                  value={network === null ? null : network ? "enabled" : "disabled"}
                />
              </div>
            </div>
          </>,
          document.body,
        )}
    </div>
  );
}

function presetDisplayLabel(requested: string | null): string | null {
  if (!requested) return null;
  const known = (PRESET_LABELS as Record<string, string>)[requested];
  return known ?? requested;
}

function Fact({ label, value }: { readonly label: string; readonly value: string | null }) {
  return (
    <div className="cc-permission-facts__row">
      <span>{label}</span>
      <code>{value ?? "unknown"}</code>
    </div>
  );
}
