import { AnimatePresence, motion } from "framer-motion";
import { CardDetail } from "./CardDetail";
import type { CardDraft } from "../../api/client";

interface ReviewModalProps {
  draft: CardDraft | null;
  currentIndex: number;
  totalCount: number;
  onAccept: () => void;
  onReject: () => void;
  onEdit: (edits: Record<string, unknown>) => void;
  onNext: () => void;
  onPrev: () => void;
  onClose: () => void;
  loading: boolean;
}

export function ReviewModal({
  draft,
  currentIndex,
  totalCount,
  onAccept,
  onReject,
  onNext,
  onPrev,
  onClose,
  loading,
}: ReviewModalProps) {
  if (!draft) return null;

  return (
    <AnimatePresence>
      <motion.div
        className="review-modal__overlay"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        onClick={onClose}
        data-testid="review-modal"
      >
        <motion.div
          className="review-modal__content"
          initial={{ y: 50, opacity: 0 }}
          animate={{ y: 0, opacity: 1 }}
          exit={{ y: 50, opacity: 0 }}
          onClick={(e) => e.stopPropagation()}
        >
          <div className="review-modal__header">
            <h2>Review Card ({currentIndex + 1}/{totalCount})</h2>
            <button
              className="review-modal__close"
              onClick={onClose}
              data-testid="close-modal"
            >
              &times;
            </button>
          </div>

          <CardDetail draft={draft} />

          <div className="review-modal__actions">
            <button
              className="review-modal__btn review-modal__btn--accept"
              onClick={onAccept}
              disabled={loading}
              data-testid="accept-button"
            >
              Accept
            </button>
            <button
              className="review-modal__btn review-modal__btn--reject"
              onClick={onReject}
              disabled={loading}
              data-testid="reject-button"
            >
              Reject
            </button>
          </div>

          {totalCount > 1 && (
            <div className="review-modal__nav">
              <button
                onClick={onPrev}
                disabled={currentIndex === 0}
                data-testid="prev-button"
              >
                &larr; Prev
              </button>
              <button
                onClick={onNext}
                disabled={currentIndex === totalCount - 1}
                data-testid="next-button"
              >
                Next &rarr;
              </button>
            </div>
          )}
        </motion.div>
      </motion.div>
    </AnimatePresence>
  );
}
