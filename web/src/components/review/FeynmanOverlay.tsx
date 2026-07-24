import { useCallback, useEffect, useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import { AnimatePresence, motion } from "framer-motion";
import type { FeynmanEvaluation, FeynmanQuestion } from "../../api/client";
import type { FeynmanPhase } from "../../stores/reviewStore";
import {
  EvaluatingPhase,
  FeedbackPhase,
  PromptPhase,
  QuestionPhase,
} from "./FeynmanPhases";
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

  useEffect(() => {
    if (phase === "prompt") {
      skipRef.current?.focus();
    } else if (phase === "question") {
      textareaRef.current?.focus();
    } else if (phase === "feedback") {
      continueRef.current?.focus();
    }
  }, [phase]);

  const previousSessionId = useRef<string | null>(null);
  useEffect(() => {
    const sessionId = question?.session_id ?? null;
    if (sessionId !== previousSessionId.current) {
      previousSessionId.current = sessionId;
      setAnswer("");
    }
  }, [question]);

  const handleSubmit = useCallback(() => {
    const trimmed = answer.trim();
    if (!trimmed) return;
    onSubmitAnswer(trimmed);
  }, [answer, onSubmitAnswer]);

  const handleKeyDown = useCallback(
    (event: KeyboardEvent<HTMLTextAreaElement>) => {
      if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
        event.preventDefault();
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
