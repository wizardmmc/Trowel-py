/**
 * FeynmanOverlay — 4-phase interactive overlay for the Feynman technique.
 *
 * Phases: prompt → question → evaluating → feedback.
 * Each phase renders a distinct region with Framer Motion transitions.
 * (023 will add a self-eval phase for LLM-unavailable degradation.)
 */

import { useRef, useEffect, useState, useCallback } from "react";
import type { KeyboardEvent, RefObject } from "react";
import { AnimatePresence, motion } from "framer-motion";
import type {
  FeynmanQuestion,
  FeynmanEvaluation,
} from "../../api/client";
import type { FeynmanPhase } from "../../stores/reviewStore";
import "./FeynmanOverlay.css";

interface FeynmanOverlayProps {
  readonly phase: FeynmanPhase;
  readonly question: FeynmanQuestion | null;
  readonly result: FeynmanEvaluation | null;
  readonly loading: boolean;
  readonly error: string | null;
  readonly onSkip: () => void;
  readonly onTryIt: () => void;
  readonly onSubmitAnswer: (answer: string) => void;
  readonly onContinue: () => void;
}

// 圆形 sunshine badge 容纳阶段图标（slice021-web：替代 🧠❓📊 emoji）
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

export function FeynmanOverlay({
  phase,
  question,
  result,
  loading,
  error,
  onSkip,
  onTryIt,
  onSubmitAnswer,
  onContinue,
}: FeynmanOverlayProps) {
  const skipRef = useRef<HTMLButtonElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const continueRef = useRef<HTMLButtonElement>(null);
  const [answer, setAnswer] = useState("");

  // Focus management per phase
  useEffect(() => {
    if (phase === "prompt") {
      skipRef.current?.focus();
    } else if (phase === "question") {
      textareaRef.current?.focus();
    } else if (phase === "feedback") {
      continueRef.current?.focus();
    }
  }, [phase]);

  // Reset the answer only when a brand-new question arrives
  const prevSessionId = useRef<string | null>(null);
  useEffect(() => {
    const newId = question?.session_id ?? null;
    if (newId !== prevSessionId.current) {
      prevSessionId.current = newId;
      setAnswer("");
    }
  }, [question]);

  const handleSubmit = useCallback(() => {
    const trimmed = answer.trim();
    if (!trimmed) return;
    onSubmitAnswer(trimmed);
  }, [answer, onSubmitAnswer]);

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        handleSubmit();
      }
    },
    [handleSubmit],
  );

  if (phase === "hidden") return null;

  return (
    <div className="feynman-overlay" data-testid="feynman-overlay">
      <motion.div
        className="feynman-overlay__card"
        initial={{ scale: 0.9, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        exit={{ scale: 0.9, opacity: 0 }}
        transition={{ duration: 0.2 }}
      >
        <AnimatePresence mode="wait">
          {phase === "prompt" && (
            <PromptPhase
              key="prompt"
              skipRef={skipRef}
              onSkip={onSkip}
              onTryIt={onTryIt}
              loading={loading}
              error={error}
            />
          )}

          {phase === "question" && question && (
            <QuestionPhase
              key="question"
              question={question}
              answer={answer}
              textareaRef={textareaRef}
              onAnswerChange={setAnswer}
              onSubmit={handleSubmit}
              onSkip={onSkip}
              onKeyDown={handleKeyDown}
            />
          )}

          {phase === "evaluating" && <EvaluatingPhase key="evaluating" />}

          {phase === "feedback" && result && (
            <FeedbackPhase
              key="feedback"
              result={result}
              continueRef={continueRef}
              onContinue={onContinue}
            />
          )}
        </AnimatePresence>
      </motion.div>
    </div>
  );
}

/* ---------- Prompt phase ---------- */

interface PromptPhaseProps {
  readonly skipRef: RefObject<HTMLButtonElement | null>;
  readonly onSkip: () => void;
  readonly onTryIt: () => void;
  readonly loading: boolean;
  readonly error: string | null;
}

function PromptPhase({
  skipRef,
  onSkip,
  onTryIt,
  loading,
  error,
}: PromptPhaseProps) {
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

/* ---------- Question phase ---------- */

interface QuestionPhaseProps {
  readonly question: FeynmanQuestion;
  readonly answer: string;
  readonly textareaRef: RefObject<HTMLTextAreaElement | null>;
  readonly onAnswerChange: (value: string) => void;
  readonly onSubmit: () => void;
  readonly onSkip: () => void;
  readonly onKeyDown: (e: KeyboardEvent<HTMLTextAreaElement>) => void;
}

function QuestionPhase({
  question,
  answer,
  textareaRef,
  onAnswerChange,
  onSubmit,
  onSkip,
  onKeyDown,
}: QuestionPhaseProps) {
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
        onChange={(e) => onAnswerChange(e.target.value)}
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

/* ---------- Evaluating phase ---------- */

function EvaluatingPhase() {
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

/* ---------- Feedback phase ---------- */

interface FeedbackPhaseProps {
  readonly result: FeynmanEvaluation;
  readonly continueRef: RefObject<HTMLButtonElement | null>;
  readonly onContinue: () => void;
}

function FeedbackPhase({
  result,
  continueRef,
  onContinue,
}: FeedbackPhaseProps) {
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
            {result.missed_points.map((point, i) => (
              <li key={`${point.slice(0, 20)}-${i}`}>{point}</li>
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

/* ---------- Score bar ---------- */

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
