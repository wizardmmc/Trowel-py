import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ProfileView } from "../components/profile/ProfileView";
import type { ProfileDTO } from "../api/client";

// Mock the HTTP layer; the store under the view runs for real, orchestrating
// state against these mocked responses.
vi.mock("../api/client", () => ({
  fetchProfile: vi.fn(),
  putProfile: vi.fn(),
}));

import { fetchProfile as fetchProfileApi, putProfile as putProfileApi } from "../api/client";

const testProfile: ProfileDTO = {
  ability: "网安硕士",
  methodology: "spec-first",
  expression: "大白话",
  goal: "反诈论文",
  other: "保形预测",
  updated: "2026-07-15",
  source: "user-edit",
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(fetchProfileApi).mockResolvedValue(testProfile);
  vi.mocked(putProfileApi).mockResolvedValue(testProfile);
});

describe("ProfileView", () => {
  it("renders five dimension sections from GET", async () => {
    render(<ProfileView />);
    await waitFor(() => expect(fetchProfileApi).toHaveBeenCalled());
    expect(screen.getByText("能力水平")).toBeInTheDocument();
    expect(screen.getByText("方法论偏好")).toBeInTheDocument();
    expect(screen.getByText("表达风格")).toBeInTheDocument();
    expect(screen.getByText("长程目标")).toBeInTheDocument();
    expect(screen.getByText("其他")).toBeInTheDocument();
  });

  it("shows the profile content in read mode", async () => {
    render(<ProfileView />);
    await waitFor(() => expect(screen.getByText("网安硕士")).toBeInTheDocument());
  });

  it("edit button enters edit mode with textareas prefilled", async () => {
    render(<ProfileView />);
    await waitFor(() => expect(screen.getByText("网安硕士")).toBeInTheDocument());
    await userEvent.click(screen.getByTestId("profile-edit-button"));
    expect(screen.getByTestId("profile-save-button")).toBeInTheDocument();
    expect(screen.getByTestId("profile-cancel-button")).toBeInTheDocument();
    expect(screen.getByTestId("profile-dim-ability")).toHaveValue("网安硕士");
  });

  it("save calls putProfile with the draft and exits edit mode", async () => {
    render(<ProfileView />);
    await waitFor(() => expect(screen.getByText("网安硕士")).toBeInTheDocument());
    await userEvent.click(screen.getByTestId("profile-edit-button"));
    const ta = screen.getByTestId("profile-dim-ability") as HTMLTextAreaElement;
    await userEvent.clear(ta);
    await userEvent.type(ta, "改过的能力");
    await userEvent.click(screen.getByTestId("profile-save-button"));
    await waitFor(() => expect(putProfileApi).toHaveBeenCalled());
    expect(putProfileApi).toHaveBeenCalledWith(
      expect.objectContaining({ ability: "改过的能力" }),
    );
    await waitFor(() =>
      expect(screen.queryByTestId("profile-save-button")).not.toBeInTheDocument(),
    );
  });

  it("save failure shows an explicit error and stays in edit mode (C-6)", async () => {
    vi.mocked(putProfileApi).mockRejectedValue(new Error("save failed"));
    render(<ProfileView />);
    await waitFor(() => expect(screen.getByText("网安硕士")).toBeInTheDocument());
    await userEvent.click(screen.getByTestId("profile-edit-button"));
    await userEvent.click(screen.getByTestId("profile-save-button"));
    await waitFor(() =>
      expect(screen.getByTestId("profile-error")).toBeInTheDocument(),
    );
    expect(screen.getByTestId("profile-error")).toHaveTextContent("save failed");
    expect(screen.getByTestId("profile-save-button")).toBeInTheDocument();
  });

  it("cancel exits edit mode without saving", async () => {
    render(<ProfileView />);
    await waitFor(() => expect(screen.getByText("网安硕士")).toBeInTheDocument());
    await userEvent.click(screen.getByTestId("profile-edit-button"));
    await userEvent.click(screen.getByTestId("profile-cancel-button"));
    expect(screen.queryByTestId("profile-save-button")).not.toBeInTheDocument();
    expect(putProfileApi).not.toHaveBeenCalled();
  });

  it("renders empty sections on cold start (all dims blank)", async () => {
    vi.mocked(fetchProfileApi).mockResolvedValue({
      ...testProfile,
      ability: "",
      methodology: "",
      expression: "",
      goal: "",
      other: "",
    });
    render(<ProfileView />);
    await waitFor(() => expect(screen.getByText("能力水平")).toBeInTheDocument());
    // all five titles present even when empty (cold-start: empty sections shown)
    expect(screen.getByText("其他")).toBeInTheDocument();
  });
});
