import { beforeEach, describe, expect, it } from "vitest";

import {
  loadNewSessionPreferences,
  saveNewSessionPreferences,
} from "../components/cc/newSessionPreferences";

const CONFIG = {
  runtime: "codex" as const,
  model: "gpt-5.6-sol",
  effort: "ultra",
  permission_mode: "",
  permission_preset: "workspace-write" as const,
  memory_enabled: false,
  profile_enabled: true,
};

describe("new session preferences", () => {
  beforeEach(() => localStorage.clear());

  it("round-trips only the reusable creation config", () => {
    saveNewSessionPreferences(CONFIG);
    expect(loadNewSessionPreferences()).toEqual(CONFIG);
  });

  it("ignores malformed or unknown-version storage", () => {
    localStorage.setItem("trowel.new-session-preferences", "not-json");
    expect(loadNewSessionPreferences()).toBeNull();
    localStorage.setItem(
      "trowel.new-session-preferences",
      JSON.stringify({ version: 99, config: CONFIG }),
    );
    expect(loadNewSessionPreferences()).toBeNull();
  });
});
