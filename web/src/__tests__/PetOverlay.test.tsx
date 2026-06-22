import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { PetOverlay } from "../components/pet/PetOverlay";
import { usePetStore } from "../stores/petStore";
import type { Pet, PetResponse } from "../api/client";

// Mock the HTTP layer so PetOverlay's mount-time fetch and the interact call
// never hit the network. fetchPet is a no-op spy here because each test injects
// the pet state directly via setState.
vi.mock("../api/client", () => ({
  fetchPet: vi.fn(),
  interactPet: vi.fn(),
  feedPet: vi.fn(),
  equipHat: vi.fn(),
}));

const testPet: Pet = {
  player_id: "default",
  mood: "normal",
  hunger: 80,
  equipped_hat: null,
  updated_at: "2026-06-22T00:00:00",
};

const testResponse: PetResponse = { text: "Hello!", mood: "happy" };

describe("PetOverlay", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.useRealTimers();
    usePetStore.setState({
      pet: testPet,
      lastResponse: null,
      loading: false,
      error: null,
      // neutralize the mount-time fetch so injected state is not overwritten
      fetchPet: vi.fn(),
    });
  });

  it("renders nothing when pet is null", () => {
    usePetStore.setState({ pet: null });
    render(<PetOverlay />);
    expect(screen.queryByTestId("pet-overlay")).toBeNull();
  });

  it("renders the pet svg when pet exists", () => {
    render(<PetOverlay />);
    expect(screen.getByTestId("pet-svg")).toBeInTheDocument();
  });

  it("calls interact when clicked and no onClick prop is given", async () => {
    const interactSpy = vi.fn().mockResolvedValue(undefined);
    usePetStore.setState({ interact: interactSpy });
    render(<PetOverlay />);
    await fireEvent.click(screen.getByTestId("pet-overlay"));
    expect(interactSpy).toHaveBeenCalledOnce();
  });

  it("calls onClick instead of interact when onClick prop is provided", async () => {
    const onClick = vi.fn();
    const interactSpy = vi.fn().mockResolvedValue(undefined);
    usePetStore.setState({ interact: interactSpy });
    render(<PetOverlay onClick={onClick} />);
    await fireEvent.click(screen.getByTestId("pet-overlay"));
    expect(onClick).toHaveBeenCalledOnce();
    expect(interactSpy).not.toHaveBeenCalled();
  });

  it("shows the speech bubble with the lastResponse text", () => {
    usePetStore.setState({ lastResponse: testResponse });
    render(<PetOverlay />);
    expect(screen.getByText("Hello!")).toBeInTheDocument();
  });

  it("auto-dismisses the speech bubble after 3 seconds", () => {
    vi.useFakeTimers();
    usePetStore.setState({ lastResponse: testResponse });
    render(<PetOverlay />);
    expect(screen.getByText("Hello!")).toBeInTheDocument();

    act(() => {
      vi.advanceTimersByTime(3000);
    });
    // after the 3s dismiss timer the bubble is no longer visible — either
    // unmounted by AnimatePresence or held at its exit state (opacity 0).
    // Whether the node leaves the DOM is an animation-timing detail.
    expect(screen.queryByText("Hello!")).not.toBeVisible();
  });
});
