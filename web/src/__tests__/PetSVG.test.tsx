import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { PetSVG } from "../components/pet/PetSVG";
import type { PetMood } from "../api/client";


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
    expect(container.querySelector("[data-hat]")).not.toBeNull();
  });
});
