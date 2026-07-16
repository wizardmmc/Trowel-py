import { describe, it, expect } from "vitest";

import { formatRunDuration } from "../components/cc/durationLabel";

describe("formatRunDuration", () => {
  it("formats sub-minute spans as Ns", () => {
    expect(formatRunDuration(1)).toBe("1s");
    expect(formatRunDuration(45)).toBe("45s");
    expect(formatRunDuration(59)).toBe("59s");
  });

  it("formats minute spans as Mm Ss", () => {
    expect(formatRunDuration(60)).toBe("1m 0s");
    expect(formatRunDuration(78)).toBe("1m 18s");
    expect(formatRunDuration(211)).toBe("3m 31s");
    expect(formatRunDuration(3599)).toBe("59m 59s");
  });

  it("formats hour spans as Hh Mm", () => {
    expect(formatRunDuration(3600)).toBe("1h 0m");
    expect(formatRunDuration(3900)).toBe("1h 5m");
    expect(formatRunDuration(7384)).toBe("2h 3m");
  });
});
