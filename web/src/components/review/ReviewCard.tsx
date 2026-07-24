import { useState, useEffect, useCallback } from "react";
import { motion } from "framer-motion";
import type { DueCard } from "../../api/client";
import "./ReviewCard.css";

interface ReviewCardProps {
  dueCard: DueCard;
  onRate: (rating: number) => void;
  disabled: boolean;
  readonly onOpenFeynman?: () => void;
}

const RATING_OPTIONS = [
  { value: 1, label: "Again", shortcut: "1", testId: "rate-again", className: "rate-btn--again" },
  { value: 2, label: "Hard", shortcut: "2", testId: "rate-hard", className: "rate-btn--hard" },
  { value: 3, label: "Good", shortcut: "3", testId: "rate-good", className: "rate-btn--good" },
  { value: 4, label: "Easy", shortcut: "4", testId: "rate-easy", className: "rate-btn--easy" },
] as const;

const DIFFICULTY_LABELS = ["", "Trivial", "Easy", "Medium", "Hard", "Expert"];

export function ReviewCard({ dueCard, onRate, disabled, onOpenFeynman }: ReviewCardProps) {
  const [flipped, setFlipped] = useState(false);

  const handleFlip = useCallback(() => {
    setFlipped(true);
  }, []);

  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if (disabled) return;
      if (e.code === "Space" && !flipped) {
        e.preventDefault();
        setFlipped(true);
        return;
      }
      if (!flipped) return;
      const match = RATING_OPTIONS.find((b) => b.shortcut === e.key);
      if (match) {
        e.preventDefault();
        onRate(match.value);
      }
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [flipped, disabled, onRate]);

  return (
    <div className="review-card">
      {!flipped ? (
        <motion.div
          data-testid="card-front"
          className="review-card__face review-card__face--front"
          initial={{ rotateY: 90, opacity: 0 }}
          animate={{ rotateY: 0, opacity: 1 }}
          transition={{ duration: 0.25 }}
        >
          <div className="review-card__category">{dueCard.card.category}</div>
          <h2 className="review-card__title">{dueCard.card.title}</h2>
          <div className="review-card__difficulty">
            {DIFFICULTY_LABELS[dueCard.card.difficulty] ??
              `Level ${dueCard.card.difficulty}`}
          </div>
          <button
            className="review-card__flip-btn"
            data-testid="flip-button"
            onClick={handleFlip}
          >
            Press <kbd>Space</kbd> to reveal
          </button>
        </motion.div>
      ) : (
        <motion.div
          data-testid="card-back"
          className="review-card__face review-card__face--back"
          initial={{ rotateY: -90, opacity: 0 }}
          animate={{ rotateY: 0, opacity: 1 }}
          transition={{ duration: 0.25 }}
        >
          <h3 className="review-card__back-title">{dueCard.card.title}</h3>
          <div className="review-card__explanation">{dueCard.card.explanation}</div>

          {dueCard.card.example && (
            <div className="review-card__example">
              <span className="review-card__example-label">Example</span>
              <pre className="review-card__example-content">{dueCard.card.example}</pre>
            </div>
          )}

          {onOpenFeynman && (
            <button
              className="btn btn--secondary review-card__feynman-btn"
              data-testid="open-feynman"
              onClick={onOpenFeynman}
              disabled={disabled}
            >
              <svg className="review-card__feynman-icon" viewBox="0 0 24 24" aria-hidden="true">
                <path d="M9 4a3 3 0 0 0-3 3 3 3 0 0 0-1 5.8V15a3 3 0 0 0 3 3 3 3 0 0 0 1 2 3 3 0 0 0 3-3V6a3 3 0 0 0-3-2zM15 4a3 3 0 0 1 3 3 3 3 0 0 1 1 5.8V15a3 3 0 0 1-3 3 3 3 0 0 1-1 2 3 3 0 0 1-3-3" />
              </svg>
              费曼讲解
            </button>
          )}

          <div className="review-card__rate-actions">
            {RATING_OPTIONS.map((opt) => (
              <motion.button
                key={opt.value}
                className={`rate-btn ${opt.className}`}
                data-testid={opt.testId}
                onClick={() => onRate(opt.value)}
                disabled={disabled}
                whileTap={{ scale: 0.92 }}
                aria-label={`${opt.label} (${opt.shortcut})`}
              >
                <span className="rate-btn__label">{opt.label}</span>
                <span className="rate-btn__shortcut">{opt.shortcut}</span>
              </motion.button>
            ))}
          </div>
        </motion.div>
      )}
    </div>
  );
}
