/** 提供 Statistics 紧凑触发器下方的 portal 浮层和统一关闭行为。 */

import {
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import "./statistics-popover.css";

interface StatisticsPopoverProps {
  /** 触发按钮的可访问名称。 */
  readonly triggerAriaLabel: string;
  /** 浮层内容的可访问名称。 */
  readonly contentAriaLabel: string;
  /** 触发按钮内部的图标与短标签。 */
  readonly triggerContent: ReactNode;
  /** 浮层正文；函数形态可取得统一关闭回调。 */
  readonly children: ReactNode | ((close: () => void) => ReactNode);
  /** 追加到触发按钮的领域样式类。 */
  readonly triggerClassName?: string;
  /** 追加到浮层容器的领域样式类。 */
  readonly contentClassName?: string;
  /** 浮层对辅助技术呈现为对话框或提示。 */
  readonly role?: "dialog" | "tooltip";
  /** 浮层与触发器左边或右边对齐。 */
  readonly align?: "start" | "end";
  /** 空间充足时使用的目标宽度，单位为 CSS 像素。 */
  readonly preferredWidth?: number;
  /** 打开或关闭后通知业务组件同步草稿等附加状态。 */
  readonly onOpenChange?: (open: boolean) => void;
}

interface PopoverPosition {
  /** 浮层相对视口的上边位置。 */
  readonly top: number;
  /** 浮层相对视口且经过边缘收敛的左边位置。 */
  readonly left: number;
  /** 根据视口收窄后的实际宽度。 */
  readonly width: number;
  /** 触发器下方可用于滚动内容的最大高度。 */
  readonly maxHeight: number;
}

/** 把紧凑触发器的内容渲染到 body，并统一处理定位、外部点击和 Escape。 */
export function StatisticsPopover({
  triggerAriaLabel,
  contentAriaLabel,
  triggerContent,
  children,
  triggerClassName = "",
  contentClassName = "",
  role = "dialog",
  align = "end",
  preferredWidth = 320,
  onOpenChange,
}: StatisticsPopoverProps) {
  const [open, setOpen] = useState(false);
  const [position, setPosition] = useState<PopoverPosition | null>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const contentId = useId();

  const close = () => {
    setOpen(false);
    setPosition(null);
    onOpenChange?.(false);
  };

  const updatePosition = () => {
    const bounds = triggerRef.current?.getBoundingClientRect();
    if (!bounds) return;
    const edge = 16;
    const width = Math.min(preferredWidth, window.innerWidth - edge * 2);
    const desiredLeft = align === "start" ? bounds.left : bounds.right - width;
    const left = Math.min(
      Math.max(edge, desiredLeft),
      Math.max(edge, window.innerWidth - width - edge),
    );
    setPosition({
      top: bounds.bottom + 6,
      left,
      width,
      maxHeight: Math.max(180, window.innerHeight - bounds.bottom - edge - 6),
    });
  };

  const toggle = () => {
    if (open) {
      close();
      return;
    }
    updatePosition();
    setOpen(true);
    onOpenChange?.(true);
  };

  useLayoutEffect(() => {
    if (open) updatePosition();
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      close();
      triggerRef.current?.focus();
    };
    const reposition = () => updatePosition();
    window.addEventListener("keydown", handleKeyDown);
    window.addEventListener("resize", reposition);
    window.addEventListener("scroll", reposition, true);
    return () => {
      window.removeEventListener("keydown", handleKeyDown);
      window.removeEventListener("resize", reposition);
      window.removeEventListener("scroll", reposition, true);
    };
  }, [open]);

  const style: CSSProperties | undefined = position
    ? {
        top: position.top,
        left: position.left,
        width: position.width,
        maxHeight: position.maxHeight,
      }
    : undefined;

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        className={`statistics-popover__trigger ${triggerClassName}`.trim()}
        aria-label={triggerAriaLabel}
        aria-expanded={open}
        aria-controls={contentId}
        aria-haspopup={role === "dialog" ? "dialog" : undefined}
        onClick={toggle}
      >
        {triggerContent}
      </button>
      {open &&
        position &&
        createPortal(
          <>
            <div
              className="statistics-popover__backdrop"
              aria-hidden="true"
              onClick={close}
            />
            <div
              id={contentId}
              className={`statistics-popover__content ${contentClassName}`.trim()}
              role={role}
              aria-label={contentAriaLabel}
              style={style}
            >
              {typeof children === "function" ? children(close) : children}
            </div>
          </>,
          document.body,
        )}
    </>
  );
}
