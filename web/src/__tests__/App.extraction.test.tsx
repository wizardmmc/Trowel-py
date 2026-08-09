/** 验证顶层提取流程只在本次请求真实成功后打开草稿审核。 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const cardStore = vi.hoisted(() => {
  const state = {
    drafts: [{ id: "old", title: "旧草稿" }],
    currentDraftIndex: 0,
    loading: false,
    error: null as string | null,
    extract: vi.fn(),
    extractConversation: vi.fn(),
    review: vi.fn(),
    nextDraft: vi.fn(),
    prevDraft: vi.fn(),
    clearDrafts: vi.fn(),
    reExplainRegens: [],
    reExplainSelectedId: "original",
    reExplainLoading: false,
    reExplainError: null,
    regenerateExplanation: vi.fn(),
    selectReExplain: vi.fn(),
    resetReExplain: vi.fn(),
  };
  return {
    state,
    useCardStore: Object.assign(() => state, { getState: () => state }),
  };
});

const addNotification = vi.hoisted(() => vi.fn());

vi.mock("../stores/cardStore", () => ({
  useCardStore: cardStore.useCardStore,
}));
vi.mock("../stores/notificationStore", () => ({
  useNotificationStore: () => ({ addNotification }),
}));
vi.mock("../stores/reviewStore", () => ({
  useReviewStore: () => ({ startSession: vi.fn(), phase: "idle" }),
}));
vi.mock("../agent", () => ({
  AgentWorkspace: () => null,
  useAgentStore: { getState: vi.fn() },
}));
vi.mock("../components/layout/AppLayout", () => ({
  AppLayout: ({
    children,
    onToolChange,
  }: {
    readonly children: ReactNode;
    readonly onToolChange: (tool: string) => void;
  }) => (
    <div>
      <button type="button" onClick={() => onToolChange("extract")}>提取</button>
      {children}
    </div>
  ),
}));
vi.mock("../components/cards/ExtractionInput", () => ({
  ExtractionInput: ({ onExtract }: { readonly onExtract: (text: string) => void }) => (
    <button type="button" onClick={() => onExtract("内容")}>开始提取</button>
  ),
}));
vi.mock("../components/cards/ReviewModal", () => ({
  ReviewModal: () => <div>草稿审核已打开</div>,
}));
vi.mock("../components/cards/NotificationBanner", () => ({
  NotificationBanner: () => null,
}));
vi.mock("../components/review/ReviewSession", () => ({ ReviewSession: () => null }));
vi.mock("../components/garden/GardenView", () => ({ GardenView: () => null }));
vi.mock("../components/profile/ProfileView", () => ({ ProfileView: () => null }));

import App from "../App";

describe("App extraction", () => {
  beforeEach(() => {
    cardStore.state.error = null;
    cardStore.state.drafts = [{ id: "old", title: "旧草稿" }];
    cardStore.state.extract.mockReset();
    addNotification.mockReset();
  });

  it("does not report success when extraction fails while old drafts remain", async () => {
    cardStore.state.extract.mockImplementation(async () => {
      cardStore.state.error = "提取失败";
    });
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "提取" }));
    fireEvent.click(screen.getByRole("button", { name: "开始提取" }));

    await waitFor(() => expect(cardStore.state.extract).toHaveBeenCalledOnce());
    expect(addNotification).not.toHaveBeenCalled();
    expect(screen.queryByText("草稿审核已打开")).toBeNull();
  });
});
