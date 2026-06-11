import { useEffect } from "react";
import { useReviewStore } from "../../stores/reviewStore";
import { ReviewCard } from "./ReviewCard";
import { ReviewCompletion } from "./ReviewCompletion";

interface ReviewSessionProps {
  onClose: () => void;
}

export function ReviewSession({ onClose }: ReviewSessionProps) {
  const {
    dueCards,
    currentIndex,
    loading,
    error,
    sessionComplete,
    sessionStats,
    loadDueCards,
    rateCard,
    resetSession,
  } = useReviewStore();

  useEffect(() => {
    loadDueCards();
  }, [loadDueCards]);

  const currentCard = dueCards[currentIndex] ?? null;

  // Session finished — show completion
  if (sessionComplete) {
    if (sessionStats && sessionStats.total > 0) {
      return (
        <ReviewCompletion
          stats={sessionStats}
          onBackToGarden={() => {
            resetSession();
            onClose();
          }}
        />
      );
    }
    // No cards were due at all
    return (
      <div className="review-session__empty">
        <p>No cards due today! 🌱</p>
        <button
          className="review-session__close-btn"
          onClick={() => {
            resetSession();
            onClose();
          }}
        >
          Back to Garden
        </button>
      </div>
    );
  }

  if (error) {
    return (
      <div className="review-session__error">
        <p>Something went wrong: {error}</p>
        <button
          className="review-session__close-btn"
          onClick={() => {
            resetSession();
            onClose();
          }}
        >
          Back to Garden
        </button>
      </div>
    );
  }

  if (!currentCard) {
    return <div className="review-session__loading">Loading cards...</div>;
  }

  return (
    <div className="review-session" data-testid="review-session">
      <div className="review-session__header">
        <span className="review-session__progress">
          {currentIndex + 1} / {dueCards.length}
        </span>
      </div>
      <ReviewCard
        dueCard={currentCard}
        onRate={rateCard}
        disabled={loading}
      />
    </div>
  );
}
