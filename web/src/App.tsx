/** 组合花园、提取、复习、Agent、研讨、统计、画像和设置顶层工具。 */

import { lazy, Suspense, useEffect, useState } from "react";
import { AppLayout, type Tool } from "./components/layout/AppLayout";
import { ExtractionInput } from "./components/cards/ExtractionInput";
import { ReviewModal } from "./components/cards/ReviewModal";
import { NotificationBanner } from "./components/cards/NotificationBanner";
import { ReviewSession } from "./components/review/ReviewSession";
import { GardenView } from "./components/garden/GardenView";
import { AgentWorkspace, useAgentStore } from "./agent";
import { ProfileView } from "./components/profile/ProfileView";
import { useCardStore } from "./stores/cardStore";
import { useNotificationStore } from "./stores/notificationStore";
import { useReviewStore } from "./stores/reviewStore";

const StatisticsWorkspace = lazy(async () => {
  const module = await import("./statistics/ui/StatisticsWorkspace");
  return { default: module.StatisticsWorkspace };
});
const SettingsWorkspace = lazy(async () => {
  const module = await import("./settings");
  return { default: module.SettingsWorkspace };
});
const DiscussionWorkspace = lazy(async () => {
  const module = await import("./discussion");
  return { default: module.DiscussionWorkspace };
});
const READ_ONLY_INSPECTION =
  import.meta.env.VITE_TROWEL_INSPECTION_MODE === "1";

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
  const [activeTool, setActiveTool] = useState<Tool>(readInitialTool);
  const [statisticsMounted, setStatisticsMounted] = useState(
    () => readInitialTool() === "statistics",
  );
  const [settingsMounted, setSettingsMounted] = useState(
    () => readInitialTool() === "settings",
  );
  const [discussionMounted, setDiscussionMounted] = useState(
    () => readInitialTool() === "discussion",
  );
  const [sidebarOpen, setSidebarOpen] = useState(false);

  const currentDraft = drafts[currentDraftIndex] ?? null;
  const reviewActive = phase !== "idle";

  useEffect(() => {
    if (drafts.length > 0 && !reviewOpen) {
      setReviewOpen(true);
    }
  }, [drafts.length]);

  useEffect(() => {
    const url = new URL(window.location.href);
    if (
      activeTool === "statistics" ||
      activeTool === "settings" ||
      activeTool === "discussion"
    ) {
      url.searchParams.set("tool", activeTool);
      if (activeTool === "settings") {
        url.searchParams.delete("statistics_tab");
        url.searchParams.delete("trace_id");
      }
    } else {
      url.searchParams.delete("tool");
      url.searchParams.delete("statistics_tab");
      url.searchParams.delete("trace_id");
    }
    window.history.replaceState(window.history.state, "", url);
  }, [activeTool]);

  const handleToolChange = (tool: Tool) => {
    if (tool === "review") {
      startSession();
    } else {
      if (tool === "statistics") setStatisticsMounted(true);
      if (tool === "settings") setSettingsMounted(true);
      if (tool === "discussion") setDiscussionMounted(true);
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
      inspectionOnly={READ_ONLY_INSPECTION}
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
      {!READ_ONLY_INSPECTION && (
        <div className="agent-workspace-slot" hidden={activeTool !== "cc"}>
          <AgentWorkspace />
        </div>
      )}
      {discussionMounted && (
        <div
          className="discussion-workspace-slot"
          hidden={activeTool !== "discussion"}
        >
          <Suspense fallback={<div role="status">正在打开研讨…</div>}>
            <DiscussionWorkspace
              active={activeTool === "discussion"}
              onAgentHandoff={async (sessionId) => {
                await useAgentStore.getState().refreshActiveSessions();
                await useAgentStore.getState().activateSession(sessionId);
                setActiveTool("cc");
              }}
            />
          </Suspense>
        </div>
      )}
      {statisticsMounted && (
        <div
          className="statistics-workspace-slot"
          hidden={activeTool !== "statistics"}
        >
          <Suspense
            fallback={
              <div className="statistics-workspace-loading" role="status">
                正在打开统计…
              </div>
            }
          >
            <StatisticsWorkspace active={activeTool === "statistics"} />
          </Suspense>
        </div>
      )}
      {settingsMounted && (
        <div className="settings-workspace-slot" hidden={activeTool !== "settings"}>
          <Suspense
            fallback={<div className="settings-workspace-loading" role="status">正在打开设置…</div>}
          >
            <SettingsWorkspace active={activeTool === "settings"} />
          </Suspense>
        </div>
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

/** 只接受已注册的一级入口，避免陈旧 URL 把应用带进空白页。 */
function readInitialTool(): Tool {
  if (READ_ONLY_INSPECTION) return "statistics";
  const requested = new URLSearchParams(window.location.search).get("tool");
  return requested === "statistics" ||
    requested === "settings" ||
    requested === "discussion"
    ? requested
    : "garden";
}

export default App;
