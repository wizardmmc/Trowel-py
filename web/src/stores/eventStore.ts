import { create } from "zustand";
import {
  triggerEvent as triggerEventApi,
  type EventLog,
} from "../api/client";

export interface EventState {
  readonly currentEvent: EventLog | null;
  readonly checking: boolean;
  readonly error: string | null;

  checkForEvent: () => Promise<void>;
  claimReward: () => void;
}

export const useEventStore = create<EventState>((set) => ({
  currentEvent: null,
  checking: false,
  error: null,

  checkForEvent: async () => {
    set({ checking: true, error: null });
    try {
      const event = await triggerEventApi();
      if (event) {
        set({ currentEvent: event, checking: false });
      } else {
        set({ checking: false });
      }
    } catch (err) {
      const message =
        err instanceof Error ? err.message : "Failed to check for event";
      set({ checking: false, error: message });
    }
  },

  claimReward: () => {
    set({ currentEvent: null });
  },
}));
