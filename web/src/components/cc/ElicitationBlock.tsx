import { useState } from "react";
import type { ElicitationItem } from "../../stores/ccStore";
import {
  ElicitationNavBar,
  ElicitationOptionRow,
} from "./ElicitationControls";

interface Props {
  readonly item: ElicitationItem;
  // 多选答案按上游协议序列化为逗号分隔字符串。
  readonly onAnswer?: (answers: Record<string, string>) => void;
  readonly onCancel?: () => void;
  // 历史回放中的旧 pending 请求不可交互。
  readonly disabled?: boolean;
}

// 与上游内置 Other 选项使用相同哨兵值。
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
  // questions.length 代表最终确认页。
  const [currentIdx, setCurrentIdx] = useState(0);
  const [selections, setSelections] = useState<
    Record<string, readonly string[]>
  >({});
  const [otherText, setOtherText] = useState<Record<string, string>>({});

  // 单个单选题沿用上游流程，跳过最终确认页。
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

  // Other 文本追加在已选标签后，多项用逗号连接。
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

  // 空白 Other 不算已回答，否则上游会拒绝空答案。
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

  if (isInSubmit) {
    return (
      <div className="cc-elicit">
        <ElicitationNavBar
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

  const q = questions[currentIdx];
  const isLast = currentIdx === questions.length - 1;
  const sel = selections[q.question] ?? [];
  return (
    <div className="cc-elicit">
      {questions.length > 1 && (
        <ElicitationNavBar
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
          <ElicitationOptionRow
            key={opt.label}
            index={i + 1}
            option={opt}
            multi={q.multiSelect}
            selected={sel.includes(opt.label)}
            disabled={disabled}
            onClick={() => toggleOption(q.question, opt.label, q.multiSelect)}
          />
        ))}
        <ElicitationOptionRow
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
