/**
 * ElicitationBlock — inline AskUserQuestion selection box (slice-025-c).
 *
 * Renders three states of an ElicitationItem:
 *   - pending: interactive selection (single-select radio / multi-select
 *     checkbox / Other free-text / multi-question NavBar / SubmitView review).
 *   - answered: cc's tool_result text echoed as a completed block.
 *   - declined: "User declined to answer questions".
 *
 * Visual reference: docs/design/front-end/ask-user-question-20260704.html.
 * Source logic mirrors cc's AskUserQuestionPermissionRequest component group
 * (QuestionView/SubmitView/QuestionNavigationBar/use-multiple-choice-state).
 *
 * Web adaptation: keyboard navigation (↑/↓/Enter/Space) becomes mouse clicks;
 * the typeahead buffer becomes the Other text input; single-select still
 * advances on submit (we keep an explicit Submit button for clarity rather
 * than cc's auto-advance-on-Enter).
 */
import { useEffect, useRef, useState } from "react";
import type { ElicitationItem } from "../../stores/ccStore";
import type { QuestionInput, QuestionOption } from "../../api/ccTypes";

interface Props {
  readonly item: ElicitationItem;
  /** Called with {questionText: answerStr} when the user submits. Multi-select
   * answers are comma-separated strings (spec/04 A.2). */
  readonly onAnswer?: (answers: Record<string, string>) => void;
  /** Called when the user declines (Esc / Cancel). */
  readonly onCancel?: () => void;
  /** Disable interaction (history replay of a stale pending item). */
  readonly disabled?: boolean;
}

/** Sentinel for the built-in "Other" option (mirrors cc's __other__). */
const OTHER_VALUE = "__other__";

export function ElicitationBlock({ item, onAnswer, onCancel, disabled }: Props) {
  if (item.status === "answered") {
    return (
      <div className="cc-elicit cc-elicit--answered">
        <div className="cc-elicit__head">● User answered Claude&apos;s questions</div>
        {item.resultText && (
          <div className="cc-elicit__result">{item.resultText}</div>
        )}
      </div>
    );
  }
  if (item.status === "declined") {
    return (
      <div className="cc-elicit cc-elicit--declined">
        <div className="cc-elicit__head">● User declined to answer questions</div>
      </div>
    );
  }
  return (
    <PendingElicit
      item={item}
      onAnswer={onAnswer}
      onCancel={onCancel}
      disabled={disabled}
    />
  );
}

/** Pending interactive state. Local UI state (current question, selections,
 * Other text) lives here; the parent only learns the final answers on submit. */
function PendingElicit({
  item,
  onAnswer,
  onCancel,
  disabled,
}: {
  readonly item: ElicitationItem;
  readonly onAnswer?: (answers: Record<string, string>) => void;
  readonly onCancel?: () => void;
  readonly disabled?: boolean;
}) {
  const questions = item.questions;
  // currentIdx ranges 0..questions.length; the length slot is the SubmitView.
  const [currentIdx, setCurrentIdx] = useState(0);
  // selections: {questionText: chosen labels[]}. Single-select still uses an
  // array (length 1) for uniform handling.
  const [selections, setSelections] = useState<
    Record<string, readonly string[]>
  >({});
  const [otherText, setOtherText] = useState<Record<string, string>>({});

  // hideSubmitTab: a single non-multiSelect question skips the SubmitView and
  // submits directly from the question view (mirrors cc's hideSubmitTab).
  const hideSubmitTab =
    questions.length === 1 && !questions[0].multiSelect;

  const isInSubmit = currentIdx === questions.length;

  const toggleOption = (
    qText: string,
    label: string,
    multi: boolean,
  ): void => {
    const cur = selections[qText] ?? [];
    let next: readonly string[];
    if (multi) {
      next = cur.includes(label)
        ? cur.filter((l) => l !== label)
        : [...cur, label];
    } else {
      next = cur.includes(label) ? [] : [label];
    }
    setSelections({ ...selections, [qText]: next });
  };

  /** Build the answers payload from current selections. Other text is appended
   * to the labels (cc serializes multi-select as comma-separated). */
  const buildAnswers = (): Record<string, string> => {
    const out: Record<string, string> = {};
    for (const q of questions) {
      const sel = selections[q.question] ?? [];
      const labels = sel.filter((l) => l !== OTHER_VALUE);
      const other = sel.includes(OTHER_VALUE)
        ? (otherText[q.question]?.trim() ?? "")
        : "";
      const parts = other ? [...labels, other] : labels;
      if (parts.length > 0) out[q.question] = parts.join(", ");
    }
    return out;
  };

  // allAnswered is derived from buildAnswers (not raw selections) so that an
  // "Other" checkbox with empty text doesn't count as answered — cc rejects an
  // empty answers record (052 ZodError). CR WARNING fix.
  const answers = buildAnswers();
  const allAnswered = questions.every((q) => q.question in answers);

  const handleSubmit = (): void => {
    if (disabled) return;
    onAnswer?.(buildAnswers());
  };
  const handleCancel = (): void => {
    if (disabled) return;
    onCancel?.();
  };

  // SubmitView (only reached when !hideSubmitTab)
  if (isInSubmit) {
    return (
      <div className="cc-elicit">
        <NavBar
          questions={questions}
          selections={selections}
          currentIdx={currentIdx}
          hideSubmitTab={hideSubmitTab}
          onJump={disabled ? undefined : setCurrentIdx}
        />
        <div className="cc-elicit__title">Review your answers</div>
        {!allAnswered && (
          <div className="cc-elicit__warn">
            ⚠ You have not answered all questions
          </div>
        )}
        <div className="cc-elicit__answers">
          {questions
            .filter((q) => (selections[q.question]?.length ?? 0) > 0)
            .map((q) => (
              <div key={q.question} className="cc-elicit__answer">
                <div className="cc-elicit__answer-q">• {q.question}</div>
                <div className="cc-elicit__answer-a">
                  → {formatAnswer(selections[q.question], otherText[q.question])}
                </div>
              </div>
            ))}
        </div>
        <div className="cc-elicit__hint">Ready to submit your answers?</div>
        <div className="cc-elicit__actions">
          <button
            type="button"
            className="cc-btn cc-btn--primary"
            onClick={handleSubmit}
            disabled={disabled || !allAnswered}
          >
            Submit answers
          </button>
          <button
            type="button"
            className="cc-btn"
            onClick={handleCancel}
            disabled={disabled}
          >
            Cancel
          </button>
        </div>
      </div>
    );
  }

  // QuestionView
  const q = questions[currentIdx];
  const isLast = currentIdx === questions.length - 1;
  const sel = selections[q.question] ?? [];
  return (
    <div className="cc-elicit">
      {questions.length > 1 && (
        <NavBar
          questions={questions}
          selections={selections}
          currentIdx={currentIdx}
          hideSubmitTab={hideSubmitTab}
          onJump={disabled ? undefined : setCurrentIdx}
        />
      )}
      <div className="cc-elicit__title">{q.question}</div>
      <div className="cc-elicit__options">
        {q.options.map((opt, i) => (
          <OptionRow
            key={opt.label}
            index={i + 1}
            option={opt}
            multi={q.multiSelect}
            selected={sel.includes(opt.label)}
            disabled={disabled}
            onClick={() => toggleOption(q.question, opt.label, q.multiSelect)}
          />
        ))}
        <OptionRow
          index={q.options.length + 1}
          option={{ label: "Other" }}
          multi={q.multiSelect}
          selected={sel.includes(OTHER_VALUE)}
          disabled={disabled}
          otherMode
          otherText={otherText[q.question] ?? ""}
          onOtherChange={(t) =>
            setOtherText({ ...otherText, [q.question]: t })
          }
          onClick={() => toggleOption(q.question, OTHER_VALUE, q.multiSelect)}
        />
      </div>
      <div className="cc-elicit__divider" />
      <button
        type="button"
        className="cc-elicit__footer"
        onClick={handleCancel}
        disabled={disabled}
        title="Skip the options and reply in natural language"
      >
        <span className="cc-elicit__opt-num">{q.options.length + 2}.</span>{" "}
        Chat about this
      </button>
      <div className="cc-elicit__actions">
        {currentIdx > 0 && (
          <button
            type="button"
            className="cc-btn"
            onClick={() => setCurrentIdx(currentIdx - 1)}
            disabled={disabled}
          >
            ← Prev
          </button>
        )}
        <button
          type="button"
          className="cc-btn cc-btn--primary"
          onClick={() =>
            isLast && hideSubmitTab
              ? handleSubmit()
              : setCurrentIdx(currentIdx + 1)
          }
          disabled={disabled || (isLast && hideSubmitTab && !allAnswered)}
        >
          {isLast && hideSubmitTab ? "Submit" : "Next →"}
        </button>
        <button
          type="button"
          className="cc-btn cc-btn--ghost"
          onClick={handleCancel}
          disabled={disabled}
        >
          Cancel
        </button>
      </div>
    </div>
  );
}

/** Multi-question navigation bar: one tab per question (checkbox + header
 * slice) + a Submit tab. Mirrors cc's QuestionNavigationBar. */
function NavBar({
  questions,
  selections,
  currentIdx,
  hideSubmitTab,
  onJump,
}: {
  readonly questions: readonly QuestionInput[];
  readonly selections: Record<string, readonly string[]>;
  readonly currentIdx: number;
  readonly hideSubmitTab: boolean;
  readonly onJump?: (idx: number) => void;
}) {
  return (
    <div className="cc-elicit__navbar">
      {questions.map((q, i) => {
        const answered = (selections[q.question]?.length ?? 0) > 0;
        const isCurrent = i === currentIdx;
        return (
          <button
            type="button"
            key={q.question}
            className={`cc-elicit__tab${isCurrent ? " cc-elicit__tab--current" : ""}`}
            onClick={onJump ? () => onJump(i) : undefined}
            disabled={!onJump}
          >
            <span className="cc-elicit__tab-chk">
              {answered ? "☑" : "☐"}
            </span>
            {q.header.slice(0, 3)}
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

/** One selectable row (radio/checkbox + label + description, plus an Other
 * text input when otherMode and selected). */
function OptionRow({
  index,
  option,
  multi,
  selected,
  disabled,
  onClick,
  otherMode,
  otherText,
  onOtherChange,
}: {
  readonly index: number;
  readonly option: QuestionOption;
  readonly multi: boolean;
  readonly selected: boolean;
  readonly disabled?: boolean;
  readonly onClick: () => void;
  readonly otherMode?: boolean;
  readonly otherText?: string;
  readonly onOtherChange?: (text: string) => void;
}) {
  const mark = multi ? (selected ? "☑" : "☐") : selected ? "●" : "○";
  // slice-035 bug1: auto-grow the Other textarea so long custom answers wrap
  // and the box grows tall, instead of the old single-line <input> that let
  // long text run off the side. height = scrollHeight on every value change.
  const otherRef = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    const ta = otherRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = `${ta.scrollHeight}px`;
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
            onChange={(e) => onOtherChange?.(e.target.value)}
            onClick={(e) => e.stopPropagation()}
            disabled={disabled}
          />
        )}
      </div>
    </div>
  );
}

/** Format a question's selection for the SubmitView review list. */
function formatAnswer(
  sel: readonly string[] | undefined,
  otherText: string | undefined,
): string {
  const labels = (sel ?? []).filter((l) => l !== OTHER_VALUE);
  const other = sel?.includes(OTHER_VALUE)
    ? (otherText?.trim() ?? "")
    : "";
  const parts = other ? [...labels, other] : labels;
  return parts.join(", ");
}
