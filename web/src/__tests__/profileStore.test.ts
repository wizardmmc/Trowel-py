import { describe, it, expect, vi, beforeEach } from "vitest";
import { useProfileStore } from "../stores/profileStore";
import type { ProfileDTO, ProfileUpdate } from "../api/client";

vi.mock("../api/client", () => ({
  fetchProfile: vi.fn(),
  putProfile: vi.fn(),
}));

import { fetchProfile as fetchProfileApi, putProfile as putProfileApi } from "../api/client";

const testProfile: ProfileDTO = {
  ability: "网安硕士 / 红队",
  methodology: "spec-first，spike 实测",
  expression: "大白话，禁翻译腔",
  goal: "反诈论文 + trowel",
  other: "在啃保形预测",
  updated: "2026-07-15",
  source: "user-edit",
};

const testUpdate: ProfileUpdate = {
  ability: "网安硕士 / 红队",
  methodology: "spec-first，spike 实测",
  expression: "大白话，禁翻译腔",
  goal: "反诈论文 + trowel",
  other: "在啃保形预测",
};

describe("profileStore", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useProfileStore.setState({
      profile: null,
      loading: false,
      error: null,
    });
  });

  it("fetchProfile loads the profile", async () => {
    vi.mocked(fetchProfileApi).mockResolvedValue(testProfile);

    await useProfileStore.getState().fetchProfile();

    expect(useProfileStore.getState().profile).toEqual(testProfile);
    expect(useProfileStore.getState().error).toBeNull();
  });

  it("fetchProfile stores the error message on failure", async () => {
    vi.mocked(fetchProfileApi).mockRejectedValue(new Error("boom"));

    await useProfileStore.getState().fetchProfile();

    expect(useProfileStore.getState().profile).toBeNull();
    expect(useProfileStore.getState().error).toBe("boom");
  });

  it("updateProfile sends PUT and stores the returned profile", async () => {
    const saved: ProfileDTO = { ...testProfile, updated: "2026-07-16" };
    vi.mocked(putProfileApi).mockResolvedValue(saved);

    await useProfileStore.getState().updateProfile(testUpdate);

    expect(putProfileApi).toHaveBeenCalledWith(testUpdate);
    expect(useProfileStore.getState().profile).toEqual(saved);
    expect(useProfileStore.getState().loading).toBe(false);
  });

  it("updateProfile rethrows on failure so the view can show the error (C-6)", async () => {
    vi.mocked(putProfileApi).mockRejectedValue(new Error("save failed"));

    await expect(
      useProfileStore.getState().updateProfile(testUpdate),
    ).rejects.toThrow("save failed");

    expect(useProfileStore.getState().error).toBe("save failed");
    expect(useProfileStore.getState().loading).toBe(false);
  });
});
