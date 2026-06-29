import { AnimatePresence, motion } from "framer-motion";
import { useReviewStore } from "../../stores/reviewStore";
import { ReviewCard } from "./ReviewCard";
import { ReviewCompletion } from "./ReviewCompletion";
import { FeynmanOverlay } from "./FeynmanOverlay";
import "./ReviewSession.css";

/**
 * Full-screen review session — mirrors the TS design (fixed inset:0 over a
 * solid bg-garden background), not a centered modal. The FeynmanOverlay sits
 * absolutely inside the card-wrapper so it covers the card, not the whole
 * screen.
 */
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
    feynman_phase,
    feynman_question,
    feynman_result,
    feynman_loading,
    feynman_error,
    openFeynman,
    tryFeynman,
    submitFeynmanAnswer,
    skipFeynman,
    continueFromFeynman,
  } = useReviewStore();

  if (phase === "idle") return null;

  const currentCard = dueCards[currentIndex] ?? null;
  const feynmanActive = feynman_phase !== "hidden";
  const isComplete = phase === "complete" || sessionComplete;
  const showCard = !isComplete && !error && currentCard !== null;

  return (
    <AnimatePresence>
      <motion.div
        className="review-session"
        data-testid="review-session"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        transition={{ duration: 0.2 }}
      >
        <button
          className="review-session__close"
          onClick={resetSession}
          aria-label="Close review session"
        >
          ✕
        </button>

        {isComplete && (
          <div className="review-session__panel">
            {sessionStats && sessionStats.total > 0 ? (
              <ReviewCompletion stats={sessionStats} onBackToGarden={resetSession} />
            ) : (
              <div className="review-session__empty">
                <div className="review-session__empty-icon" aria-hidden="true">
                  <svg className="review-session__empty-svg" viewBox="0 0 24 24">
                    <circle cx="12" cy="12" r="3" />
                    <path d="M12 2a10 10 0 1 0 10 10" />
                  </svg>
                </div>
                <p>今天没有需要复习的卡片</p>
                <button className="btn btn--primary" onClick={resetSession}>
                  返回花园
                </button>
              </div>
            )}
          </div>
        )}

        {error && !isComplete && (
          <div className="review-session__panel review-session__error">
            <p>出错了：{error}</p>
            <button className="btn btn--primary" onClick={resetSession}>
              返回花园
            </button>
          </div>
        )}

        {!isComplete && !error && loading && currentCard === null && (
          <div className="review-session__loading">
            <div className="review-session__spinner" />
            <p>正在加载复习卡片…</p>
          </div>
        )}

        {showCard && (
          <div className="review-session__card-area">
            <div className="review-session__progress">
              <span>
                {currentIndex + 1} / {dueCards.length}
              </span>
              <div className="review-session__progress-bar">
                <div
                  className="review-session__progress-fill"
                  style={{
                    width: `${((currentIndex + 1) / dueCards.length) * 100}%`,
                  }}
                />
              </div>
            </div>
            <div className="review-session__card-wrapper">
              <ReviewCard
                key={currentCard.card.id}
                dueCard={currentCard}
                onRate={rateCard}
                disabled={loading || feynmanActive}
                onOpenFeynman={openFeynman}
              />
              <FeynmanOverlay
                phase={feynman_phase}
                question={feynman_question}
                result={feynman_result}
                loading={feynman_loading}
                error={feynman_error}
                onSkip={skipFeynman}
                onTryIt={tryFeynman}
                onSubmitAnswer={submitFeynmanAnswer}
                onContinue={continueFromFeynman}
              />
            </div>
          </div>
        )}
      </motion.div>
    </AnimatePresence>
  );
}
