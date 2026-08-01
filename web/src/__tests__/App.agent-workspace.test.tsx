/** 验证顶层工具切换不会卸载 renderer 本地 Agent 工作区。 */

import { fireEvent, render, screen } from "@testing-library/react";
import { useState, type ReactNode } from "react";
import { expect, it, vi } from "vitest";

vi.mock("../components/layout/AppLayout", () => ({
  AppLayout: ({
    children,
    onToolChange,
  }: {
    readonly children: ReactNode;
    readonly onToolChange: (tool: string) => void;
  }) => (
    <div>
      <button type="button" onClick={() => onToolChange("garden")}>花园</button>
      <button type="button" onClick={() => onToolChange("cc")}>Agent</button>
      {children}
    </div>
  ),
}));

vi.mock("../agent", () => ({
  AgentWorkspace: () => {
    const [workdir, setWorkdir] = useState("");
    return (
      <input
        aria-label="测试工作区"
        value={workdir}
        onChange={(event) => setWorkdir(event.target.value)}
      />
    );
  },
}));

vi.mock("../stores/cardStore", () => ({
  useCardStore: () => ({
    drafts: [],
    currentDraftIndex: 0,
    loading: false,
    extract: vi.fn(),
    extractConversation: vi.fn(),
    review: vi.fn(),
    nextDraft: vi.fn(),
    prevDraft: vi.fn(),
    clearDrafts: vi.fn(),
    reExplainRegens: [],
    reExplainSelectedId: null,
    reExplainLoading: false,
    reExplainError: null,
    regenerateExplanation: vi.fn(),
    selectReExplain: vi.fn(),
    resetReExplain: vi.fn(),
  }),
}));

vi.mock("../stores/notificationStore", () => ({
  useNotificationStore: () => ({ addNotification: vi.fn() }),
}));

vi.mock("../stores/reviewStore", () => ({
  useReviewStore: () => ({ startSession: vi.fn(), phase: "idle" }),
}));

vi.mock("../components/cards/ExtractionInput", () => ({
  ExtractionInput: () => null,
}));
vi.mock("../components/cards/ReviewModal", () => ({ ReviewModal: () => null }));
vi.mock("../components/cards/NotificationBanner", () => ({
  NotificationBanner: () => null,
}));
vi.mock("../components/review/ReviewSession", () => ({
  ReviewSession: () => null,
}));
vi.mock("../components/garden/GardenView", () => ({ GardenView: () => null }));
vi.mock("../components/profile/ProfileView", () => ({ ProfileView: () => null }));

import App from "../App";

it("keeps the selected Agent workspace while visiting another tool", () => {
  render(<App />);
  fireEvent.click(screen.getByRole("button", { name: "Agent" }));
  fireEvent.change(screen.getByLabelText("测试工作区"), {
    target: { value: "/repo" },
  });

  fireEvent.click(screen.getByRole("button", { name: "花园" }));
  fireEvent.click(screen.getByRole("button", { name: "Agent" }));

  expect(screen.getByLabelText("测试工作区")).toHaveValue("/repo");
});
