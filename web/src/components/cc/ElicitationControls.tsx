import { useEffect, useRef } from "react";

import type { QuestionInput, QuestionOption } from "../../api/ccTypes";

interface ElicitationNavBarProps {
  readonly questions: readonly QuestionInput[];
  readonly selections: Record<string, readonly string[]>;
  readonly currentIdx: number;
  readonly hideSubmitTab: boolean;
  readonly onJump?: (index: number) => void;
}

export function ElicitationNavBar({
  questions,
  selections,
  currentIdx,
  hideSubmitTab,
  onJump,
}: ElicitationNavBarProps) {
  return (
    <div className="cc-elicit__navbar">
      {questions.map((question, index) => {
        const answered =
          (selections[question.question]?.length ?? 0) > 0;
        const isCurrent = index === currentIdx;
        return (
          <button
            type="button"
            key={question.question}
            className={`cc-elicit__tab${isCurrent ? " cc-elicit__tab--current" : ""}`}
            onClick={onJump ? () => onJump(index) : undefined}
            disabled={!onJump}
          >
            <span className="cc-elicit__tab-chk">
              {answered ? "☑" : "☐"}
            </span>
            {question.header.slice(0, 3)}
          </button>
        );
      })}
      {!hideSubmitTab && (
        <button
          type="button"
          className={`cc-elicit__tab cc-elicit__tab--submit${
            currentIdx === questions.length ? " cc-elicit__tab--current" : ""
          }`}
          onClick={onJump ? () => onJump(questions.length) : undefined}
          disabled={!onJump}
        >
          <span className="cc-elicit__tab-chk">✓</span> Submit
        </button>
      )}
      <span className="cc-elicit__navbar-arrow">→</span>
    </div>
  );
}

interface ElicitationOptionRowProps {
  readonly index: number;
  readonly option: QuestionOption;
  readonly multi: boolean;
  readonly selected: boolean;
  readonly disabled?: boolean;
  readonly onClick: () => void;
  readonly otherMode?: boolean;
  readonly otherText?: string;
  readonly onOtherChange?: (text: string) => void;
}

export function ElicitationOptionRow({
  index,
  option,
  multi,
  selected,
  disabled,
  onClick,
  otherMode,
  otherText,
  onOtherChange,
}: ElicitationOptionRowProps) {
  const mark = multi ? (selected ? "☑" : "☐") : selected ? "●" : "○";
  const otherRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const textarea = otherRef.current;
    if (!textarea) return;
    textarea.style.height = "auto";
    textarea.style.height = `${textarea.scrollHeight}px`;
  }, [otherText, otherMode, selected]);

  return (
    <div
      className={`cc-elicit__opt${selected ? " cc-elicit__opt--selected" : ""}`}
      onClick={disabled ? undefined : onClick}
      role="option"
      aria-selected={selected}
    >
      <span className="cc-elicit__opt-num">{index}.</span>
      <span className="cc-elicit__opt-mark">{mark}</span>
      <div className="cc-elicit__opt-body">
        <div className="cc-elicit__opt-label">{option.label}</div>
        {option.description && (
          <div className="cc-elicit__opt-desc">{option.description}</div>
        )}
        {otherMode && selected && (
          <textarea
            ref={otherRef}
            className="cc-elicit__opt-other"
            rows={1}
            placeholder="Type a custom answer…"
            aria-label="Custom answer"
            value={otherText ?? ""}
            onChange={(event) => onOtherChange?.(event.target.value)}
            onClick={(event) => event.stopPropagation()}
            disabled={disabled}
          />
        )}
      </div>
    </div>
  );
}
