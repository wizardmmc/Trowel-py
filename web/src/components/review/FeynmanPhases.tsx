import type { KeyboardEvent, RefObject } from "react";
import { motion } from "framer-motion";
import type { FeynmanEvaluation, FeynmanQuestion } from "../../api/client";

function FeynmanBadge({ name }: { readonly name: "brain" | "question" | "chart" }) {
  return (
    <div className="feynman-overlay__badge" data-testid="feynman-badge">
      <svg className="feynman-overlay__badge-svg" viewBox="0 0 24 24" aria-hidden="true">
        {name === "brain" && (
          <path d="M9 4a3 3 0 0 0-3 3 3 3 0 0 0-1 5.8V15a3 3 0 0 0 3 3 3 3 0 0 0 1 2 3 3 0 0 0 3-3V6a3 3 0 0 0-3-2zM15 4a3 3 0 0 1 3 3 3 3 0 0 1 1 5.8V15a3 3 0 0 1-3 3 3 3 0 0 1-1 2 3 3 0 0 1-3-3" />
        )}
        {name === "question" && (
          <>
            <path d="M9.5 9a2.5 2.5 0 1 1 3.5 2.3c-.8.4-1 .9-1 1.7" />
            <circle cx="12" cy="16.5" r="0.6" fill="currentColor" stroke="none" />
          </>
        )}
        {name === "chart" && (
          <>
            <path d="M4 20h16" />
            <path d="M7 20v-6M12 20V8M17 20v-9" />
          </>
        )}
      </svg>
    </div>
  );
}

export function PromptPhase({
  skipRef,
  onSkip,
  onTryIt,
  loading,
  error,
}: {
  readonly skipRef: RefObject<HTMLButtonElement | null>;
  readonly onSkip: () => void;
  readonly onTryIt: () => void;
  readonly loading: boolean;
  readonly error: string | null;
}) {
  return (
    <motion.div
      className="feynman-phase feynman-phase--prompt"
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -10 }}
      transition={{ duration: 0.15 }}
    >
      <div className="feynman-overlay__badge-wrap">
        <FeynmanBadge name="brain" />
      </div>
      <p className="feynman-overlay__prompt">想测试一下你的理解吗？</p>
      {error && <p className="feynman-overlay__error">{error}</p>}
      <div className="feynman-overlay__actions">
        <button ref={skipRef} className="btn btn--secondary" onClick={onSkip}>
          跳过
        </button>
        <button
          className="btn btn--primary"
          onClick={onTryIt}
          disabled={loading}
        >
          {loading ? "加载中..." : "试一下"}
        </button>
      </div>
    </motion.div>
  );
}

export function QuestionPhase({
  question,
  answer,
  textareaRef,
  onAnswerChange,
  onSubmit,
  onSkip,
  onKeyDown,
}: {
  readonly question: FeynmanQuestion;
  readonly answer: string;
  readonly textareaRef: RefObject<HTMLTextAreaElement | null>;
  readonly onAnswerChange: (value: string) => void;
  readonly onSubmit: () => void;
  readonly onSkip: () => void;
  readonly onKeyDown: (event: KeyboardEvent<HTMLTextAreaElement>) => void;
}) {
  return (
    <motion.div
      className="feynman-phase feynman-phase--question"
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -10 }}
      transition={{ duration: 0.15 }}
    >
      <div className="feynman-overlay__badge-wrap">
        <FeynmanBadge name="question" />
      </div>
      <p className="feynman-overlay__question-text">{question.question}</p>
      {question.hint && (
        <p className="feynman-overlay__hint">
          <span className="feynman-overlay__hint-label">提示：</span>{" "}
          {question.hint}
        </p>
      )}
      <textarea
        ref={textareaRef}
        className="feynman-overlay__textarea"
        placeholder="用你自己的话解释..."
        value={answer}
        onChange={(event) => onAnswerChange(event.target.value)}
        onKeyDown={onKeyDown}
        rows={4}
      />
      <p className="feynman-overlay__submit-hint">⌘ / Ctrl + Enter 提交</p>
      <div className="feynman-overlay__actions">
        <button className="btn btn--secondary" onClick={onSkip}>
          跳过
        </button>
        <button
          className="btn btn--primary"
          onClick={onSubmit}
          disabled={answer.trim().length === 0}
        >
          提交
        </button>
      </div>
    </motion.div>
  );
}

export function EvaluatingPhase() {
  return (
    <motion.div
      className="feynman-phase feynman-phase--evaluating"
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -10 }}
      transition={{ duration: 0.15 }}
    >
      <div className="feynman-overlay__spinner" />
      <p className="feynman-overlay__evaluating-text">评估中...</p>
    </motion.div>
  );
}

export function FeedbackPhase({
  result,
  continueRef,
  onContinue,
}: {
  readonly result: FeynmanEvaluation;
  readonly continueRef: RefObject<HTMLButtonElement | null>;
  readonly onContinue: () => void;
}) {
  return (
    <motion.div
      className="feynman-phase feynman-phase--feedback"
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -10 }}
      transition={{ duration: 0.15 }}
    >
      <div className="feynman-overlay__badge-wrap">
        <FeynmanBadge name="chart" />
      </div>

      <div className="feynman-overlay__scores">
        <ScoreBar label="准确度" value={result.accuracy} />
        <ScoreBar label="完整度" value={result.completeness} />
      </div>

      <p className="feynman-overlay__feedback-text">{result.feedback}</p>

      {result.missed_points.length > 0 && (
        <div className="feynman-overlay__missed">
          <p className="feynman-overlay__missed-label">遗漏的知识点:</p>
          <ul className="feynman-overlay__missed-list">
            {result.missed_points.map((point, index) => (
              <li key={`${point.slice(0, 20)}-${index}`}>{point}</li>
            ))}
          </ul>
        </div>
      )}

      <div className="feynman-overlay__actions">
        <button
          ref={continueRef}
          className="btn btn--primary"
          onClick={onContinue}
        >
          继续
        </button>
      </div>
    </motion.div>
  );
}

function ScoreBar({
  label,
  value,
}: {
  readonly label: string;
  readonly value: number;
}) {
  const clamped = Math.max(0, Math.min(100, Math.round(value)));
  let colorClass = "score-bar__fill--low";
  if (clamped >= 70) colorClass = "score-bar__fill--high";
  else if (clamped >= 40) colorClass = "score-bar__fill--mid";

  return (
    <div className="score-bar">
      <span className="score-bar__label">{label}</span>
      <div className="score-bar__track">
        <motion.div
          className={`score-bar__fill ${colorClass}`}
          initial={{ width: 0 }}
          animate={{ width: `${clamped}%` }}
          transition={{ duration: 0.5, ease: "easeOut" }}
        />
      </div>
      <span className="score-bar__value">{clamped}</span>
    </div>
  );
}
