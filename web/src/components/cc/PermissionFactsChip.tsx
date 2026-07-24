import { useEffect, useRef, useState, type CSSProperties } from "react";
import { createPortal } from "react-dom";

interface PermissionFactsChipProps {
  readonly requested: string | null;
  readonly profile: string | null;
  readonly sandbox: string | null;
  readonly approval: string | null;
  readonly network: boolean | null;
  readonly label: string | null;
}

export function PermissionFactsChip({
  requested,
  profile,
  sandbox,
  approval,
  network,
  label,
}: PermissionFactsChipProps) {
  const [open, setOpen] = useState(false);
  const [anchor, setAnchor] = useState<{ top: number; left: number } | null>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const effectiveDanger =
    sandbox === "danger-full-access" && approval === "never";
  const requestedDanger = requested === "danger-full-access";
  const danger = effectiveDanger || requestedDanger;
  const display =
    label ??
    (requested === "danger-full-access"
      ? "Full access"
      : requested ?? "follow");
  const accessibleDisplay =
    label ??
    (requested === "danger-full-access"
      ? "Full access（待 native 确认）"
      : requested ?? "follow");

  function close(): void {
    setOpen(false);
    setAnchor(null);
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
        aria-label={`permission: ${accessibleDisplay}（查看 effective policy）`}
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
              <Fact label="requested" value={requested} />
              <Fact label="profile" value={profile} />
              <Fact label="sandbox" value={sandbox} />
              <Fact label="approval" value={approval} />
              <Fact
                label="network"
                value={network === null ? null : network ? "enabled" : "disabled"}
              />
            </div>
          </>,
          document.body,
        )}
    </div>
  );
}

function Fact({ label, value }: { readonly label: string; readonly value: string | null }) {
  return (
    <div className="cc-permission-facts__row">
      <span>{label}</span>
      <code>{value ?? "unknown"}</code>
    </div>
  );
}
