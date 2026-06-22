import { create } from "zustand";
import {
  fetchPet as fetchPetApi,
  interactPet as interactPetApi,
  feedPet as feedPetApi,
  equipHat as equipHatApi,
  type Pet,
  type PetResponse,
} from "../api/client";

export interface PetState {
  /** the single pet owned by the default player, or null before first load */
  readonly pet: Pet | null;
  /** the pet's last spoken line, shown in the speech bubble */
  readonly lastResponse: PetResponse | null;
  readonly loading: boolean;
  readonly error: string | null;

  fetchPet: () => Promise<void>;
  interact: () => Promise<void>;
  feed: (itemId: string) => Promise<void>;
  equipHat: (itemId: string) => Promise<void>;
}

// API functions are imported with an `Api` suffix so the store actions
// (fetchPet, equipHat, ...) don't shadow them inside the action bodies.
export const usePetStore = create<PetState>((set) => ({
  pet: null,
  lastResponse: null,
  loading: false,
  error: null,

  fetchPet: async () => {
    set({ loading: true, error: null });
    try {
      const pet = await fetchPetApi();
      set({ pet, loading: false });
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to load pet";
      set({ loading: false, error: message });
    }
  },

  interact: async () => {
    set({ error: null });
    try {
      const result = await interactPetApi();
      set({ lastResponse: result.response, pet: result.pet });
    } catch (err) {
      const message =
        err instanceof Error ? err.message : "Pet interaction failed";
      set({ error: message });
    }
  },

  feed: async (itemId: string) => {
    set({ error: null });
    try {
      const pet = await feedPetApi(itemId);
      set({ pet });
    } catch (err) {
      const message = err instanceof Error ? err.message : "Feed failed";
      set({ error: message });
    }
  },

  equipHat: async (itemId: string) => {
    set({ error: null });
    try {
      const pet = await equipHatApi(itemId);
      set({ pet });
    } catch (err) {
      const message = err instanceof Error ? err.message : "Equip failed";
      set({ error: message });
    }
  },
}));
