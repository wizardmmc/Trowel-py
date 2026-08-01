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
  createCcStore,
  useCcStore,
} from "../stores/ccStore";
import { useCcStoreFrameSelector } from "../stores/ccFrameSelector";
import { MessageList as LegacyMessageList } from "../components/cc/MessageList";
import { SessionView as LegacySessionView } from "../components/cc/SessionView";
import { WorkdirPicker as LegacyWorkdirPicker } from "../components/cc/WorkdirPicker";

describe("Agent public facade", () => {
  it("keeps one store implementation behind the Agent and cc names", () => {
    expect(useCcStore).toBe(useAgentStore);
    expect(createCcStore).toBe(createAgentStore);
    expect(useCcStoreFrameSelector).toBe(useAgentStoreFrameSelector);
  });

  it("keeps legacy UI imports pointed at the Agent owner", () => {
    expect(LegacySessionView).toBe(SessionView);
    expect(LegacyMessageList).toBe(MessageList);
    expect(LegacyWorkdirPicker).toBe(WorkdirPicker);
  });
});
