import { useState, useEffect } from "react";
import { AppLayout, type Tool } from "./components/layout/AppLayout";
import { ExtractionInput } from "./components/cards/ExtractionInput";
import { ReviewModal } from "./components/cards/ReviewModal";
import { NotificationBanner } from "./components/cards/NotificationBanner";
import { ReviewSession } from "./components/review/ReviewSession";
import { GardenView } from "./components/garden/GardenView";
import { SessionView } from "./components/cc/SessionView";
import { ProfileView } from "./components/profile/ProfileView";
import { WorkdirPicker } from "./components/cc/WorkdirPicker";
import { useCardStore } from "./stores/cardStore";
import { useNotificationStore } from "./stores/notificationStore";
import { useReviewStore } from "./stores/reviewStore";

// slice-027: default workdir is ClaudeDesktop (loads its .claude/ hooks/memory/
// skills), but the user can switch to any directory via WorkdirPicker. Recent
// workdirs persist to localStorage so the chips reappear across reloads.
const CC_WORKDIR_DEFAULT = "/Users/hamxf/VirtualVolumn/ClaudeDesktop";
const WORKDIR_STORAGE_KEY = "trowel.cc.workdirs.recent";

function loadRecentWorkdirs(): string[] {
  try {
    const raw = localStorage.getItem(WORKDIR_STORAGE_KEY);
    const arr = raw ? (JSON.parse(raw) as unknown) : null;
    if (Array.isArray(arr) && arr.every((x) => typeof x === "string")) {
      return arr as string[];
    }
  } catch {
    // fall through to default
  }
  return [CC_WORKDIR_DEFAULT];
}

function saveRecentWorkdir(p: string): string[] {
  const cur = loadRecentWorkdirs().filter((x) => x !== p);
  const next = [p, ...cur].slice(0, 10);
  try {
    localStorage.setItem(WORKDIR_STORAGE_KEY, JSON.stringify(next));
  } catch {
    // storage unavailable — in-memory only this session
  }
  return next;
}

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
  // slice-027: CC workdir is user-selectable; recent workdirs persist locally.
  const [ccWorkdir, setCcWorkdir] = useState<string>(CC_WORKDIR_DEFAULT);
  const [showWorkdirPicker, setShowWorkdirPicker] = useState(false);
  const [recentWorkdirs, setRecentWorkdirs] = useState<string[]>(() =>
    loadRecentWorkdirs(),
  );

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
      {!reviewActive && activeTool === "profile" && <ProfileView />}
      {activeTool === "cc" && (
        <SessionView
          workdir={ccWorkdir}
          onRequestChangeWorkdir={() => setShowWorkdirPicker(true)}
        />
      )}
      {showWorkdirPicker && (
        <WorkdirPicker
          initialPath={ccWorkdir.endsWith("/") ? ccWorkdir : `${ccWorkdir}/`}
          recents={recentWorkdirs}
          onSelect={(p) => {
            setCcWorkdir(p);
            setRecentWorkdirs(saveRecentWorkdir(p));
            setShowWorkdirPicker(false);
          }}
          onCancel={() => setShowWorkdirPicker(false)}
        />
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
