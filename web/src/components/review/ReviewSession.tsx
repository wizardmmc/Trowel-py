import { useReviewStore } from "../../stores/reviewStore";
import { ReviewCard } from "./ReviewCard";
import { ReviewCompletion } from "./ReviewCompletion";

export function ReviewSession() {
  const {
    phase,
    dueCards,
    currentIndex,
    loading,
    error,
    sessionComplete,
    sessionStats,
    rateCard,
    resetSession,
  } = useReviewStore();

  if (phase === "idle") return null;

  const currentCard = dueCards[currentIndex] ?? null;

  if (phase === "complete" || sessionComplete) {
    if (sessionStats && sessionStats.total > 0) {
      return (
        <ReviewCompletion
          stats={sessionStats}
          onBackToGarden={resetSession}
        />
      );
    }
    return (
      <div className="review-session__overlay">
        <div className="review-session__empty">
          <p>No cards due today! 🌱</p>
          <button
            className="review-session__close-btn"
            onClick={resetSession}
          >
            Back to Garden
          </button>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="review-session__overlay">
        <div className="review-session__error">
          <p>Something went wrong: {error}</p>
          <button
            className="review-session__close-btn"
            onClick={resetSession}
          >
            Back to Garden
          </button>
        </div>
      </div>
    );
  }

  if (!currentCard) {
    return null;
  }

  return (
    <div className="review-session__overlay">
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
    </div>
  );
}
