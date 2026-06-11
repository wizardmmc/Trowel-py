import { useState, useEffect, useCallback } from "react";
import { motion } from "framer-motion";
import type { DueCard } from "../../api/client";

interface ReviewCardProps {
  dueCard: DueCard;
  onRate: (rating: number) => void;
  disabled: boolean;
}

const RATING_BUTTONS = [
  { rating: 1, label: "Again", key: "1", testId: "rate-again", className: "review-card__btn--again" },
  { rating: 2, label: "Hard", key: "2", testId: "rate-hard", className: "review-card__btn--hard" },
  { rating: 3, label: "Good", key: "3", testId: "rate-good", className: "review-card__btn--good" },
  { rating: 4, label: "Easy", key: "4", testId: "rate-easy", className: "review-card__btn--easy" },
] as const;

export function ReviewCard({ dueCard, onRate, disabled }: ReviewCardProps) {
  const [flipped, setFlipped] = useState(false);

  // Reset flip state when card changes
  useEffect(() => {
    setFlipped(false);
  }, [dueCard.card.id]);

  const handleFlip = useCallback(() => {
    if (!flipped) setFlipped(true);
  }, [flipped]);

  // Keyboard shortcuts: Space=flip, 1-4=rate (only when flipped)
  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if (e.code === "Space" && !flipped) {
        e.preventDefault();
        setFlipped(true);
        return;
      }
      if (!flipped) return;
      const match = RATING_BUTTONS.find((b) => b.key === e.key);
      if (match && !disabled) {
        onRate(match.rating);
      }
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [flipped, disabled, onRate]);

  return (
    <div className="review-card">
      <div className="review-card__counter">
        <span className="review-card__plant">{dueCard.plant_stage}</span>
      </div>

      <div className="review-card__flip-container">
        {!flipped ? (
          <div data-testid="card-front" className="review-card__front">
            <h2 className="review-card__title">{dueCard.card.title}</h2>
            <span className="review-card__category">{dueCard.card.category}</span>
            <div className="review-card__tags">
              {dueCard.card.tags.map((tag) => (
                <span key={tag} className="review-card__tag">{tag}</span>
              ))}
            </div>
            <button
              className="review-card__flip-btn"
              data-testid="flip-button"
              onClick={handleFlip}
            >
              Press Space to reveal
            </button>
          </div>
        ) : (
          <motion.div
            data-testid="card-back"
            className="review-card__back"
            initial={{ rotateY: -90, opacity: 0 }}
            animate={{ rotateY: 0, opacity: 1 }}
            transition={{ duration: 0.25 }}
          >
            <h3 className="review-card__title">{dueCard.card.title}</h3>
            <p className="review-card__explanation">{dueCard.card.explanation}</p>
            {dueCard.card.example && (
              <p className="review-card__example">{dueCard.card.example}</p>
            )}
          </motion.div>
        )}
      </div>

      {flipped && (
        <motion.div
          className="review-card__actions"
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.2, delay: 0.1 }}
        >
          {RATING_BUTTONS.map((btn) => (
            <button
              key={btn.rating}
              className={`review-card__btn ${btn.className}`}
              data-testid={btn.testId}
              onClick={() => onRate(btn.rating)}
              disabled={disabled}
            >
              {btn.label}
              <span className="review-card__btn-key">{btn.key}</span>
            </button>
          ))}
        </motion.div>
      )}
    </div>
  );
}
