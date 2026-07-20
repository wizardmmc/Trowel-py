import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";

import { RateLimitBanner } from "../components/cc/RateLimitBanner";
import {
  REACHED_TYPE_LABEL,
  formatResetCountdown,
  rateLimitLevel,
} from "../components/cc/rateLimit";
import type { RateLimitSnapshot } from "../api/ccTypes";

/** The real 2026-07-18 fixture shape (usedPercent:20, not reached). */
const FIXTURE_LOW: RateLimitSnapshot = {
  limit_id: "codex",
  limit_name: null,
  primary: { usedPercent: 20, windowDurationMins: 10080, resetsAt: 1784949908 },
  secondary: null,
  credits: { hasCredits: false, unlimited: false, balance: "0" },
  individual_limit: null,
  spend_control_reached: null,
  plan_type: "pro",
  rate_limit_reached_type: null,
};

describe("rateLimitLevel (slice-077)", () => {
  it("returns null for a null snapshot (no banner)", () => {
    expect(rateLimitLevel(null)).toBeNull();
  });

  it("returns null for the real fixture's low-usage rolling update", () => {
    // spec C-6: the 20% snapshot must not pop a banner — that would cry wolf
    // on every sparse update.
    expect(rateLimitLevel(FIXTURE_LOW)).toBeNull();
  });

  it("returns 'near' when primary.usedPercent crosses the threshold", () => {
    expect(
      rateLimitLevel({ ...FIXTURE_LOW, primary: { ...FIXTURE_LOW.primary!, usedPercent: 84 } }),
    ).toBe("near");
  });

  it("returns 'near' when only secondary crosses the threshold (primary idle)", () => {
    // primary and secondary are independent windows; a high secondary must
    // warn even when primary is low (codex review HIGH).
    expect(
      rateLimitLevel({
        ...FIXTURE_LOW,
        primary: { ...FIXTURE_LOW.primary!, usedPercent: 12 },
        secondary: { usedPercent: 85, windowDurationMins: 10080, resetsAt: 2_000_000 },
      }),
    ).toBe("near");
  });

  it("returns 'reached' when rate_limit_reached_type is set, regardless of usedPercent", () => {
    const reached: RateLimitSnapshot = {
      ...FIXTURE_LOW,
      rate_limit_reached_type: "rate_limit_reached",
    };
    expect(rateLimitLevel(reached)).toBe("reached");
  });

  it("treats 80% as the near boundary (inclusive)", () => {
    const at: RateLimitSnapshot = {
      ...FIXTURE_LOW,
      primary: { ...FIXTURE_LOW.primary!, usedPercent: 80 },
    };
    expect(rateLimitLevel(at)).toBe("near");
    const below: RateLimitSnapshot = {
      ...FIXTURE_LOW,
      primary: { ...FIXTURE_LOW.primary!, usedPercent: 79 },
    };
    expect(rateLimitLevel(below)).toBeNull();
  });
});

describe("formatResetCountdown (slice-077)", () => {
  // resetsAt = 1_000_000 (unix seconds); nowMs anchors relative to it.
  const RESET_AT = 1_000_000;

  it("formats sub-day remaining as +H:MM", () => {
    // 3h 5m before reset
    const nowMs = (RESET_AT - (3 * 3600 + 5 * 60)) * 1000;
    expect(formatResetCountdown(RESET_AT, nowMs)).toBe("+3:05");
  });

  it("formats day-scale remaining as +Dd Hh", () => {
    // 2d 14h before reset
    const nowMs = (RESET_AT - (2 * 86400 + 14 * 3600)) * 1000;
    expect(formatResetCountdown(RESET_AT, nowMs)).toBe("+2d 14h");
  });

  it("clamps an expired window to +0:00 (no negative countdown)", () => {
    const nowMs = (RESET_AT + 60) * 1000; // 1 minute past reset
    expect(formatResetCountdown(RESET_AT, nowMs)).toBe("+0:00");
  });

  it("zero-pads minutes under 10", () => {
    const nowMs = (RESET_AT - (1 * 3600 + 3 * 60)) * 1000;
    expect(formatResetCountdown(RESET_AT, nowMs)).toBe("+1:03");
  });
});

describe("REACHED_TYPE_LABEL (slice-077)", () => {
  it("covers every RateLimitReachedType wire value from account.rs", () => {
    expect(REACHED_TYPE_LABEL["rate_limit_reached"]).toBe("常规速率限制触顶");
    expect(REACHED_TYPE_LABEL["workspace_owner_credits_depleted"]).toBe(
      "工作区额度耗尽（所有者）",
    );
    expect(REACHED_TYPE_LABEL["workspace_member_credits_depleted"]).toBe(
      "工作区额度耗尽（成员）",
    );
    expect(REACHED_TYPE_LABEL["workspace_owner_usage_limit_reached"]).toBe(
      "工作区用量上限（所有者）",
    );
    expect(REACHED_TYPE_LABEL["workspace_member_usage_limit_reached"]).toBe(
      "工作区用量上限（成员）",
    );
  });
});

describe("RateLimitBanner rendering (slice-077)", () => {
  it("renders nothing for a null snapshot", () => {
    const { container } = render(<RateLimitBanner snapshot={null} />);
    expect(container.firstChild).toBeNull();
  });

  it("renders nothing for the real fixture's 20% rolling update (no cry wolf)", () => {
    const { container } = render(<RateLimitBanner snapshot={FIXTURE_LOW} />);
    expect(container.firstChild).toBeNull();
  });

  it("renders the near banner when primary.usedPercent >= 80 and not reached", () => {
    const near: RateLimitSnapshot = {
      ...FIXTURE_LOW,
      primary: { ...FIXTURE_LOW.primary!, usedPercent: 84 },
    };
    render(<RateLimitBanner snapshot={near} />);
    expect(screen.getByText("接近速率限制")).toBeInTheDocument();
    // near is a status, not an alert (not terminal)
    expect(screen.queryByText("已触发速率限制")).not.toBeInTheDocument();
    expect(screen.getByText(/窗口 84%/)).toBeInTheDocument();
  });

  it("renders the reached banner with the matched reached_type label", () => {
    const reached: RateLimitSnapshot = {
      ...FIXTURE_LOW,
      rate_limit_reached_type: "rate_limit_reached",
    };
    render(<RateLimitBanner snapshot={reached} />);
    expect(screen.getByText("已触发速率限制")).toBeInTheDocument();
    expect(screen.getByText("常规速率限制触顶")).toBeInTheDocument();
    // reached is a heads-up (status), not an emergency alert — a rate limit
    // is not an "act now" condition like a turn failure (codex review M2).
    expect(screen.getByRole("status")).toBeInTheDocument();
  });

  it("falls back to the raw wire value for an unknown reached_type", () => {
    // forward-compat: a future account.rs tag surfaces verbatim instead of
    // being hidden as "no limit".
    const reached: RateLimitSnapshot = {
      ...FIXTURE_LOW,
      rate_limit_reached_type: "some_future_limit_kind",
    };
    render(<RateLimitBanner snapshot={reached} />);
    expect(screen.getByText("some_future_limit_kind")).toBeInTheDocument();
  });

  it("renders reached with no primary window (future reached_type without primary)", () => {
    // The 2026-07-18 fixture always carries primary, but the protocol makes it
    // nullable — a future workspace-owner-credits kind could land without one.
    // The title + reached_type must still render; no window row.
    const reached: RateLimitSnapshot = {
      ...FIXTURE_LOW,
      primary: null,
      rate_limit_reached_type: "workspace_owner_credits_depleted",
    };
    render(<RateLimitBanner snapshot={reached} />);
    expect(screen.getByText("已触发速率限制")).toBeInTheDocument();
    expect(screen.getByText("工作区额度耗尽（所有者）")).toBeInTheDocument();
    expect(screen.queryByText(/^primary$/)).not.toBeInTheDocument();
  });

  it("shows both primary and secondary windows when the snapshot carries both", () => {
    // secondary is null in the 2026-07-18 fixture; the renderer must still
    // honor a real secondary once a future recording carries one.
    const both: RateLimitSnapshot = {
      ...FIXTURE_LOW,
      primary: { usedPercent: 84, windowDurationMins: 300, resetsAt: 1_000_000 },
      secondary: { usedPercent: 41, windowDurationMins: 10080, resetsAt: 2_000_000 },
    };
    render(<RateLimitBanner snapshot={both} />);
    const labels = screen.getAllByText(/^primary$|^secondary$/).map((el) => el.textContent);
    expect(labels).toContain("primary");
    expect(labels).toContain("secondary");
  });

  it("shows the resets_at countdown on a window", () => {
    const near: RateLimitSnapshot = {
      ...FIXTURE_LOW,
      primary: { usedPercent: 84, windowDurationMins: 300, resetsAt: 1_000_000 },
    };
    render(<RateLimitBanner snapshot={near} />);
    // countdown format is "+H:MM" or "+Dd Hh"; assert the leading "+" and "resets"
    expect(screen.getByText(/resets \+/)).toBeInTheDocument();
  });

  it("omits the countdown when resetsAt is null (no fabrication, spec C-4)", () => {
    const near: RateLimitSnapshot = {
      ...FIXTURE_LOW,
      primary: { usedPercent: 84, windowDurationMins: null, resetsAt: null },
    };
    render(<RateLimitBanner snapshot={near} />);
    expect(screen.queryByText(/resets/)).not.toBeInTheDocument();
  });
});
