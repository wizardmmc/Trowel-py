import { describe, expect, it } from "vitest";

import {
  createAgentStore,
  MessageList,
  SessionView,
  useAgentStore,
  useAgentStoreFrameSelector,
  WorkdirPicker,
} from "../agent";
import {
  createAgentStore as ownerCreateAgentStore,
  useAgentStore as ownerUseAgentStore,
  useAgentStoreFrameSelector as ownerFrameSelector,
} from "../agent/application";
import {
  MessageList as OwnerMessageList,
  SessionView as OwnerSessionView,
  WorkdirPicker as OwnerWorkdirPicker,
} from "../agent/ui";

describe("Agent public facade", () => {
  it("points the public facade at the application owner", () => {
    expect(useAgentStore).toBe(ownerUseAgentStore);
    expect(createAgentStore).toBe(ownerCreateAgentStore);
    expect(useAgentStoreFrameSelector).toBe(ownerFrameSelector);
  });

  it("points the public facade at the UI owner", () => {
    expect(OwnerSessionView).toBe(SessionView);
    expect(OwnerMessageList).toBe(MessageList);
    expect(OwnerWorkdirPicker).toBe(WorkdirPicker);
  });
});
