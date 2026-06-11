import { ExtractionInput } from "./components/cards/ExtractionInput";
import { ReviewModal } from "./components/cards/ReviewModal";
import { NotificationBanner } from "./components/cards/NotificationBanner";
import { ReviewSession } from "./components/review/ReviewSession";
import { useCardStore } from "./stores/cardStore";
import { useNotificationStore } from "./stores/notificationStore";
import { useState } from "react";

function App() {
  const {
    drafts,
    currentDraftIndex,
    loading,
    extract,
    review,
    nextDraft,
    prevDraft,
    clearDrafts,
  } = useCardStore();
  const { addNotification } = useNotificationStore();
  const [showModal, setShowModal] = useState(false);
  const [showReview, setShowReview] = useState(false);

  const currentDraft = drafts[currentDraftIndex] ?? null;

  const handleExtract = async (content: string) => {
    await extract(content);
    if (drafts.length > 0) {
      setShowModal(true);
      addNotification("Cards extracted successfully", "success");
    }
  };

  const handleAccept = async () => {
    if (!currentDraft) return;
    await review(currentDraft.id, "accept");
    addNotification(`Accepted: ${currentDraft.title}`, "success");
    if (drafts.length <= 1) {
      setShowModal(false);
    }
  };

  const handleReject = async () => {
    if (!currentDraft) return;
    await review(currentDraft.id, "reject");
    addNotification(`Rejected: ${currentDraft.title}`, "warning");
    if (drafts.length <= 1) {
      setShowModal(false);
    }
  };

  return (
    <div className="app">
      <header className="app__header">
        <h1 className="app__title">Trowel</h1>
        <span className="app__subtitle">Knowledge Garden</span>
      </header>

      <NotificationBanner
        count={drafts.length}
        onClick={() => setShowModal(true)}
      />

      <main className="app__main">
        {showReview ? (
          <ReviewSession onClose={() => setShowReview(false)} />
        ) : (
          <>
            <ExtractionInput
              onExtract={handleExtract}
              loading={loading}
            />
            <button
              className="app__review-btn"
              onClick={() => setShowReview(true)}
              data-testid="start-review-btn"
            >
              🔄 Start Review
            </button>
          </>
        )}
      </main>

      {showModal && (
        <ReviewModal
          draft={currentDraft}
          currentIndex={currentDraftIndex}
          totalCount={drafts.length}
          onAccept={handleAccept}
          onReject={handleReject}
          onEdit={(edits) => {
            if (currentDraft) review(currentDraft.id, "edit", edits);
          }}
          onNext={nextDraft}
          onPrev={prevDraft}
          onClose={() => {
            setShowModal(false);
            clearDrafts();
          }}
          loading={loading}
        />
      )}
    </div>
  );
}

export default App;
