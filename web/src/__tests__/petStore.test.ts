import { describe, it, expect, vi, beforeEach } from "vitest";
import { usePetStore } from "../stores/petStore";
import type { Pet, PetResponse } from "../api/client";

// Mock the HTTP layer; the store under test orchestrates state, not network.
vi.mock("../api/client", () => ({
  fetchPet: vi.fn(),
  interactPet: vi.fn(),
  feedPet: vi.fn(),
  equipHat: vi.fn(),
}));

import { fetchPet, interactPet, feedPet, equipHat } from "../api/client";

const testPet: Pet = {
  player_id: "default",
  mood: "happy",
  hunger: 70,
  equipped_hat: null,
  updated_at: "2026-06-22T00:00:00",
};

describe("petStore", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    usePetStore.setState({
      pet: null,
      lastResponse: null,
      loading: false,
      error: null,
    });
  });

  it("fetchPet loads the pet", async () => {
    vi.mocked(fetchPet).mockResolvedValue(testPet);

    await usePetStore.getState().fetchPet();

    expect(usePetStore.getState().pet).toEqual(testPet);
    expect(usePetStore.getState().error).toBeNull();
  });

  it("fetchPet stores the error message on failure", async () => {
    vi.mocked(fetchPet).mockRejectedValue(new Error("boom"));

    await usePetStore.getState().fetchPet();

    expect(usePetStore.getState().pet).toBeNull();
    expect(usePetStore.getState().error).toBe("boom");
  });

  it("interact sets lastResponse and pet from the result", async () => {
    const response: PetResponse = { text: "hi", mood: "happy" };
    vi.mocked(interactPet).mockResolvedValue({ response, pet: testPet });

    await usePetStore.getState().interact();

    expect(usePetStore.getState().lastResponse).toEqual(response);
    expect(usePetStore.getState().pet).toEqual(testPet);
  });

  it("interact swallows failure into the error field", async () => {
    vi.mocked(interactPet).mockRejectedValue(new Error("nope"));

    await usePetStore.getState().interact();

    expect(usePetStore.getState().lastResponse).toBeNull();
    expect(usePetStore.getState().error).toBe("nope");
  });

  it("equipHat updates the pet with the equipped result", async () => {
    const withHat: Pet = { ...testPet, equipped_hat: "row-1" };
    vi.mocked(equipHat).mockResolvedValue(withHat);

    await usePetStore.getState().equipHat("row-1");

    expect(equipHat).toHaveBeenCalledWith("row-1");
    expect(usePetStore.getState().pet).toEqual(withHat);
  });

  it("feed updates the pet with the fed result", async () => {
    const fed: Pet = { ...testPet, hunger: 90 };
    vi.mocked(feedPet).mockResolvedValue(fed);

    await usePetStore.getState().feed("item-1");

    expect(feedPet).toHaveBeenCalledWith("item-1");
    expect(usePetStore.getState().pet).toEqual(fed);
  });
});
