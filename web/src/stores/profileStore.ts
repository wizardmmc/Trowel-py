import { create } from "zustand";
import {
  fetchProfile as fetchProfileApi,
  putProfile as putProfileApi,
  type ProfileDTO,
  type ProfileUpdate,
} from "../api/client";

export interface ProfileState {
  /** the user self-description profile, or null before first load */
  readonly profile: ProfileDTO | null;
  readonly loading: boolean;
  readonly error: string | null;

  fetchProfile: () => Promise<void>;
  /** write the five dims back; rethrows on failure so the view can show the
   * error explicitly (C-6) */
  updateProfile: (input: ProfileUpdate) => Promise<void>;
}

export const useProfileStore = create<ProfileState>((set) => ({
  profile: null,
  loading: false,
  error: null,

  fetchProfile: async () => {
    set({ loading: true, error: null });
    try {
      const profile = await fetchProfileApi();
      set({ profile, loading: false });
    } catch (err) {
      const message =
        err instanceof Error ? err.message : "Failed to load profile";
      set({ loading: false, error: message });
    }
  },

  updateProfile: async (input: ProfileUpdate) => {
    set({ loading: true, error: null });
    try {
      // PUT returns the freshly loaded profile (server-stamped updated/source),
      // so the store uses it directly — no second GET needed.
      const profile = await putProfileApi(input);
      set({ profile, loading: false });
    } catch (err) {
      const message =
        err instanceof Error ? err.message : "Failed to save profile";
      set({ loading: false, error: message });
      throw err;
    }
  },
}));
