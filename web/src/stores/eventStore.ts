import { create } from "zustand";
import {
  triggerEvent as triggerEventApi,
  type EventLog,
} from "../api/client";

export interface EventState {
  /** the event currently shown in the modal, or null when none is open */
  readonly currentEvent: EventLog | null;
  /** true while a checkForEvent request is in flight */
  readonly checking: boolean;
  readonly error: string | null;

  /** ask the backend to run one event cycle; pop the modal if something fired */
  checkForEvent: () => Promise<void>;
  /** dismiss the current event (closing the modal) */
  claimReward: () => void;
}

// The API function is imported with an `Api` suffix so the checkForEvent action
// isn't shadowed by it inside the action body (same pattern as petStore).
export const useEventStore = create<EventState>((set) => ({
  currentEvent: null,
  checking: false,
  error: null,

  checkForEvent: async () => {
    set({ checking: true, error: null });
    try {
      const event = await triggerEventApi();
      // null = nothing fired this turn (cooldown / no eligible event). Leave
      // any already-open event alone; only show a new one when we got one.
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
