import { describe, expect, it } from "vitest";

import { scrubUserText } from "../components/cc/scrubUserText";

describe("scrubUserText (slice-035 bug4 FE defense)", () => {
  it("passes real user input through unchanged", () => {
    expect(scrubUserText("修复重载渲染的几个 bug")).toBe("修复重载渲染的几个 bug");
  });

  it("restores raw slash-command tags to /name args", () => {
    const raw =
      "<command-message>grill-me</command-message>\n" +
      "<command-name>/grill-me</command-name>\n" +
      "<command-args>准备实现 slice-035</command-args>";
    expect(scrubUserText(raw)).toBe("/grill-me 准备实现 slice-035");
  });

  it("restores command tags with empty args to bare /name", () => {
    const raw =
      "<command-name>/model</command-name>\n" +
      "<command-message>model</command-message>\n" +
      "<command-args></command-args>";
    expect(scrubUserText(raw)).toBe("/model");
  });

  it("restores the trowel skill-trigger expansion to /name args", () => {
    expect(scrubUserText("Use the Skill tool with skill='grill-me'. 修复bug")).toBe(
      "/grill-me 修复bug",
    );
  });

  it("drops local-command-stdout", () => {
    expect(
      scrubUserText(
        "<local-command-stdout>Set model to glm-5.1 and saved</local-command-stdout>",
      ),
    ).toBe("");
  });

  it("drops residual system-reminder / cparam wrappers", () => {
    expect(scrubUserText("<system-reminder>internal</system-reminder>")).toBe("");
    expect(scrubUserText("<cparam>x</cparam>")).toBe("");
  });

  it("returns empty string for empty input", () => {
    expect(scrubUserText("")).toBe("");
  });
});
