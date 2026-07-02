import { useState, useEffect } from "react";
import { AppLayout, type Tool } from "./components/layout/AppLayout";
import { ExtractionInput } from "./components/cards/ExtractionInput";
import { ReviewModal } from "./components/cards/ReviewModal";
import { NotificationBanner } from "./components/cards/NotificationBanner";
import { ReviewSession } from "./components/review/ReviewSession";
import { GardenView } from "./components/garden/GardenView";
import { SessionView } from "./components/cc/SessionView";
import { useCardStore } from "./stores/cardStore";
import { useNotificationStore } from "./stores/notificationStore";
import { useReviewStore } from "./stores/reviewStore";

// CC workdir is a parameter (the workdir-picker is a separate slice). Default
// to ClaudeDesktop so CC loads its .claude/ hooks/memory/skills.
const CC_WORKDIR = "/Users/hamxf/VirtualVolumn/ClaudeDesktop";

function App() {
  const {
    drafts,
    currentDraftIndex,
    loading,
    extract,
    extractConversation,
    review,
    nextDraft,
    prevDraft,
    clearDrafts,
    reExplainRegens,
    reExplainSelectedId,
    reExplainLoading,
    reExplainError,
    regenerateExplanation,
    selectReExplain,
    resetReExplain,
  } = useCardStore();
  const { addNotification } = useNotificationStore();
  const { startSession, phase } = useReviewStore();
  const [reviewOpen, setReviewOpen] = useState(false);
  const [activeTool, setActiveTool] = useState<Tool>("garden");
  const [sidebarOpen, setSidebarOpen] = useState(false);

  const currentDraft = drafts[currentDraftIndex] ?? null;
  const reviewActive = phase !== "idle";

  useEffect(() => {
    if (drafts.length > 0 && !reviewOpen) {
      setReviewOpen(true);
    }
  }, [drafts.length]);

  const handleToolChange = (tool: Tool) => {
    if (tool === "review") {
      startSession();
    } else {
      setActiveTool(tool);
    }
    setSidebarOpen(false);
  };

  const handleExtract = async (content: string) => {
    await extract(content);
    if (drafts.length > 0) {
      addNotification("卡片提取成功", "success");
    }
  };

  const handleExtractConversation = async (content: string) => {
    await extractConversation(content);
    if (drafts.length > 0) {
      addNotification("已从会话提取卡片", "success");
    }
  };

  const handleAccept = async () => {
    if (!currentDraft) return;
    await review(currentDraft.id, "accept");
    addNotification(`已采纳：${currentDraft.title}`, "success");
    if (drafts.length <= 1) {
      setReviewOpen(false);
    }
  };

  const handleReject = async () => {
    if (!currentDraft) return;
    await review(currentDraft.id, "reject");
    addNotification(`已拒绝：${currentDraft.title}`, "warning");
    if (drafts.length <= 1) {
      setReviewOpen(false);
    }
  };

  return (
    <AppLayout
      activeTool={activeTool}
      onToolChange={handleToolChange}
      sidebarOpen={sidebarOpen}
      onToggleSidebar={() => setSidebarOpen((o) => !o)}
    >
      <NotificationBanner
        count={drafts.length}
        onClick={() => setReviewOpen(true)}
      />

      {!reviewActive && activeTool === "garden" && (
        <GardenView onStartReview={() => startSession()} />
      )}
      {!reviewActive && activeTool === "extract" && (
        <ExtractionInput
          onExtract={handleExtract}
          onExtractConversation={handleExtractConversation}
          loading={loading}
        />
      )}
      {activeTool === "cc" && (
        <SessionView workdir={CC_WORKDIR} />
      )}

      <ReviewSession />

      {reviewOpen && (
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
            setReviewOpen(false);
            clearDrafts();
          }}
          loading={loading}
          reExplainRegens={reExplainRegens}
          reExplainSelectedId={reExplainSelectedId}
          reExplainLoading={reExplainLoading}
          reExplainError={reExplainError}
          onRegenerate={(hint) => {
            if (currentDraft) regenerateExplanation(currentDraft, hint);
          }}
          onSelectCandidate={selectReExplain}
          onResetReExplain={resetReExplain}
        />
      )}
    </AppLayout>
  );
}

export default App;
