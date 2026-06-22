import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { PetSVG } from "../components/pet/PetSVG";
import type { PetMood } from "../api/client";

// PetSVG is a pure presentational component: it draws the pet's head, ears,
// blush, a mood-based expression, and an optional hat. These tests lock the
// render contract so a broken SVG path or a missing expression is caught.

const MOODS: PetMood[] = ["happy", "excited", "curious", "normal"];
const HATS = ["hat_straw", "hat_scholar", "hat_wreath"];

describe("PetSVG", () => {
  it("renders an svg with the fixed 48x48 viewBox", () => {
    render(<PetSVG mood="normal" />);
    const svg = screen.getByTestId("pet-svg");
    expect(svg).toBeInTheDocument();
    expect(svg).toHaveAttribute("viewBox", "0 0 48 48");
  });

  it.each(MOODS)("renders the %s expression group", (mood) => {
    const { container } = render(<PetSVG mood={mood} />);
    // the active expression is wrapped in a group tagged with its mood so the
    // test can tell the four expressions apart without sniffing raw path data
    expect(container.querySelector(`[data-mood="${mood}"]`)).not.toBeNull();
  });

  it.each(HATS)("renders the %s hat when equipped", (hat) => {
    const { container } = render(<PetSVG mood="happy" equippedHat={hat} />);
    expect(container.querySelector(`[data-hat="${hat}"]`)).not.toBeNull();
  });

  it("renders no hat group when equippedHat is undefined", () => {
    const { container } = render(<PetSVG mood="normal" />);
    expect(container.querySelector("[data-hat]")).toBeNull();
  });

  it("falls back to rendering a hat for an unknown hat id", () => {
    const { container } = render(
      <PetSVG mood="normal" equippedHat="hat_unknown" />,
    );
    // unknown ids fall back to the straw hat rather than rendering nothing,
    // so some hat group must still be present
    expect(container.querySelector("[data-hat]")).not.toBeNull();
  });
});
