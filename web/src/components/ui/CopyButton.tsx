/** 统一展示复制动作及其短时成功反馈，并允许页面选择品牌色或中性色。 */

import "./copy-button.css";

export interface CopyButtonProps {
  readonly ariaLabel: string;
  readonly copied: boolean;
  readonly onClick: () => void;
  readonly tone?: "brand" | "neutral";
  readonly idleLabel?: string;
  readonly copiedLabel?: string;
}

export function CopyButton({
  ariaLabel,
  copied,
  onClick,
  tone = "neutral",
  idleLabel = "复制",
  copiedLabel = "已复制",
}: CopyButtonProps) {
  return (
    <button
      type="button"
      className={`ui-copy-button ui-copy-button--${tone}`}
      data-state={copied ? "copied" : "idle"}
      onClick={onClick}
      aria-label={copied ? `${ariaLabel}，已复制` : ariaLabel}
      aria-live="polite"
    >
      {copied ? copiedLabel : idleLabel}
    </button>
  );
}
