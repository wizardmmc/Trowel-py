import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SuggestionsModal } from "../components/profile/SuggestionsModal";
import { useSuggestionsStore } from "../stores/suggestionsStore";
import { useProfileStore } from "../stores/profileStore";
import type { ProfileDTO, Suggestion } from "../api/client";

vi.mock("../api/client", () => ({
  getSuggestions: vi.fn(),
  patchSuggestionStatus: vi.fn(),
  fetchProfile: vi.fn(),
  putProfile: vi.fn(),
}));

import {
  patchSuggestionStatus as patchApi,
  putProfile as putApi,
} from "../api/client";

const profile: ProfileDTO = {
  ability: "已有能力",
  methodology: "",
  expression: "",
  goal: "",
  other: "",
  updated: "2026-07-15",
  source: "user-edit",
};

const sug = (over: Partial<Suggestion> = {}): Suggestion => ({
  id: "s1",
  dimension: "ability",
  body: "新增能力",
  sources: ["sess-abc"],
  date: "2026-07-14",
  status: "pending",
  ...over,
});

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(patchApi).mockResolvedValue(undefined);
  vi.mocked(putApi).mockResolvedValue(profile);
});

describe("SuggestionsModal", () => {
  it("renders nothing when closed", () => {
    useSuggestionsStore.setState({ suggestions: [] });
    render(<SuggestionsModal open={false} onClose={() => {}} />);
    expect(screen.queryByTestId("suggestions-modal")).not.toBeInTheDocument();
  });

  it("groups suggestions by dimension and shows bodies + sources", () => {
    useSuggestionsStore.setState({
      suggestions: [
        sug({ id: "s1", dimension: "ability", body: "能力A" }),
        sug({ id: "s2", dimension: "goal", body: "目标B" }),
      ],
    });
    useProfileStore.setState({ profile });
    render(<SuggestionsModal open={true} onClose={() => {}} />);
    expect(screen.getByText("能力水平")).toBeInTheDocument();
    expect(screen.getByText("长程目标")).toBeInTheDocument();
    expect(screen.getByTestId("suggestion-body-s1")).toHaveTextContent("能力A");
    expect(screen.getByTestId("suggestion-body-s2")).toHaveTextContent("目标B");
    expect(screen.getAllByText("sess-abc")).toHaveLength(2);
  });

  it("shows empty state when there are no suggestions", () => {
    useSuggestionsStore.setState({ suggestions: [] });
    useProfileStore.setState({ profile });
    render(<SuggestionsModal open={true} onClose={() => {}} />);
    expect(screen.getByText("暂无新建议")).toBeInTheDocument();
  });

  it("accept appends selected to the profile (PUT source=ai-calibration) + marks accepted", async () => {
    useSuggestionsStore.setState({
      suggestions: [
        sug({ id: "s1", dimension: "ability", body: "新增能力" }),
      ],
    });
    useProfileStore.setState({ profile });
    render(<SuggestionsModal open={true} onClose={() => {}} />);

    await userEvent.click(screen.getByTestId("suggestion-check-s1"));
    await userEvent.click(screen.getByTestId("suggestions-accept"));

    await waitFor(() => expect(putApi).toHaveBeenCalled());
    expect(putApi).toHaveBeenCalledWith(
      expect.objectContaining({
        ability: "已有能力\n新增能力",
        source: "ai-calibration",
      }),
    );
    expect(patchApi).toHaveBeenCalledWith("s1", "accepted");
  });

  it("accept button is disabled until something is checked", () => {
    useSuggestionsStore.setState({ suggestions: [sug()] });
    useProfileStore.setState({ profile });
    render(<SuggestionsModal open={true} onClose={() => {}} />);
    expect(screen.getByTestId("suggestions-accept")).toBeDisabled();
  });

  it("discard marks the suggestion discarded without touching the profile", async () => {
    useSuggestionsStore.setState({ suggestions: [sug({ id: "s1" })] });
    useProfileStore.setState({ profile });
    render(<SuggestionsModal open={true} onClose={() => {}} />);

    await userEvent.click(screen.getByTestId("suggestion-discard-s1"));

    await waitFor(() =>
      expect(patchApi).toHaveBeenCalledWith("s1", "discarded"),
    );
    expect(putApi).not.toHaveBeenCalled();
  });

  it("edited body is what gets appended on accept", async () => {
    useSuggestionsStore.setState({
      suggestions: [
        sug({ id: "s1", dimension: "ability", body: "原始" }),
      ],
    });
    useProfileStore.setState({ profile });
    render(<SuggestionsModal open={true} onClose={() => {}} />);

    await userEvent.click(screen.getByTestId("suggestion-edit-s1"));
    const editor = screen.getByTestId(
      "suggestion-editor-s1",
    ) as HTMLTextAreaElement;
    await userEvent.clear(editor);
    await userEvent.type(editor, "改过的");
    await userEvent.click(screen.getByTestId("suggestion-edit-s1"));
    await userEvent.click(screen.getByTestId("suggestion-check-s1"));
    await userEvent.click(screen.getByTestId("suggestions-accept"));

    await waitFor(() => expect(putApi).toHaveBeenCalled());
    expect(putApi).toHaveBeenCalledWith(
      expect.objectContaining({ ability: "已有能力\n改过的" }),
    );
  });

  it("accept failure shows an explicit error (C-6)", async () => {
    vi.mocked(putApi).mockRejectedValue(new Error("写回失败"));
    useSuggestionsStore.setState({ suggestions: [sug({ id: "s1" })] });
    useProfileStore.setState({ profile });
    render(<SuggestionsModal open={true} onClose={() => {}} />);

    await userEvent.click(screen.getByTestId("suggestion-check-s1"));
    await userEvent.click(screen.getByTestId("suggestions-accept"));

    await waitFor(() =>
      expect(screen.getByTestId("suggestions-error")).toBeInTheDocument(),
    );
    expect(screen.getByTestId("suggestions-error")).toHaveTextContent("写回失败");
  });

  it("if a mark fails after PUT, body stays written + error shows + suggestion stays visible", async () => {
    vi.mocked(patchApi).mockRejectedValue(new Error("标记失败"));
    vi.mocked(putApi).mockResolvedValue(profile);
    useSuggestionsStore.setState({ suggestions: [sug({ id: "s1" })] });
    useProfileStore.setState({ profile });
    render(<SuggestionsModal open={true} onClose={() => {}} />);

    await userEvent.click(screen.getByTestId("suggestion-check-s1"));
    await userEvent.click(screen.getByTestId("suggestions-accept"));

    await waitFor(() => expect(putApi).toHaveBeenCalled());
    expect(patchApi).toHaveBeenCalledWith("s1", "accepted");
    expect(screen.getByTestId("suggestions-error")).toBeInTheDocument();
    expect(screen.getByTestId("suggestion-body-s1")).toBeInTheDocument();
  });
});
