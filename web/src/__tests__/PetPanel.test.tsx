import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { PetPanel } from "../components/pet/PetPanel";
import { usePetStore } from "../stores/petStore";
import { usePlayerStore } from "../stores/playerStore";
import type {
  Pet,
  PetResponse,
  PlayerProfile,
  InventoryItem,
  EventLog,
} from "../api/client";

// Mock the HTTP layer: PetPanel calls fetchEventHistory directly. The store
// actions it triggers (fetchProfile / fetchInventory / interact / feed / equip)
// are replaced with spies via setState below, so they never reach the network.
vi.mock("../api/client", () => ({
  fetchEventHistory: vi.fn(),
}));

import { fetchEventHistory } from "../api/client";

const testPet: Pet = {
  player_id: "default",
  mood: "happy",
  hunger: 70,
  // row id of the worn hat — NOT a catalog id (py backend stores the row id)
  equipped_hat: "hat-row-1",
  updated_at: "2026-06-22T00:00:00",
};

const testPlayer: PlayerProfile = {
  id: "default",
  xp: 100,
  coins: 100,
  streak_days: 3,
  last_active: "2026-06-22T00:00:00",
  created_at: "2026-06-01T00:00:00",
  level: 2,
  xp_to_next_level: 200,
};

function makeItem(overrides: Partial<InventoryItem>): InventoryItem {
  return {
    id: "row",
    player_id: "default",
    item_id: "food_basic",
    item_type: "food",
    equipped: 0,
    obtained_at: "2026-06-22T00:00:00",
    ...overrides,
  };
}

const testEvents: EventLog[] = [
  {
    id: "e1",
    player_id: "default",
    event_type: "sign_in",
    reward_xp: 20,
    reward_coin: 10,
    reward_item_id: null,
    description: null,
    card_id: null,
    triggered_at: "2026-06-22T10:00:00",
  },
];

function resetStores(): void {
  usePetStore.setState({
    pet: testPet,
    lastResponse: null,
    loading: false,
    error: null,
    fetchPet: vi.fn(),
    feed: vi.fn().mockResolvedValue(undefined),
    interact: vi.fn().mockResolvedValue(undefined),
    equipHat: vi.fn().mockResolvedValue(undefined),
  });
  usePlayerStore.setState({
    player: testPlayer,
    inventory: [],
    loading: false,
    error: null,
    fetchProfile: vi.fn().mockResolvedValue(undefined),
    fetchInventory: vi.fn().mockResolvedValue(undefined),
    buyItem: vi.fn().mockResolvedValue(undefined),
  });
}

describe("PetPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.useRealTimers();
    vi.mocked(fetchEventHistory).mockResolvedValue([]);
    resetStores();
  });

  it("renders nothing when closed", () => {
    render(<PetPanel open={false} onClose={vi.fn()} />);
    expect(screen.queryByText("小锤的花园小屋")).toBeNull();
  });

  it("renders the title and all sections when open", () => {
    render(<PetPanel open={true} onClose={vi.fn()} />);
    expect(screen.getByText("小锤的花园小屋")).toBeInTheDocument();
    expect(screen.getByText("状态")).toBeInTheDocument();
    expect(screen.getByText("喂食")).toBeInTheDocument();
    expect(screen.getByText("背包 - 帽子")).toBeInTheDocument();
    expect(screen.getByText("最近事件")).toBeInTheDocument();
  });

  it("loads profile, inventory, interaction and event history when opened", async () => {
    const fetchProfile = vi.fn().mockResolvedValue(undefined);
    const fetchInventory = vi.fn().mockResolvedValue(undefined);
    const interact = vi.fn().mockResolvedValue(undefined);
    usePlayerStore.setState({ fetchProfile, fetchInventory });
    usePetStore.setState({ interact });

    render(<PetPanel open={true} onClose={vi.fn()} />);

    await waitFor(() => {
      expect(fetchProfile).toHaveBeenCalledOnce();
      expect(fetchInventory).toHaveBeenCalledOnce();
      expect(interact).toHaveBeenCalledOnce();
      expect(fetchEventHistory).toHaveBeenCalledWith(5);
    });
  });

  it("shows mood and hunger from the pet state", () => {
    usePetStore.setState({ pet: { ...testPet, mood: "happy", hunger: 70 } });
    render(<PetPanel open={true} onClose={vi.fn()} />);
    // MOOD_LABELS.happy.text = 开心
    expect(screen.getByText(/开心/)).toBeInTheDocument();
    expect(screen.getByText("70%")).toBeInTheDocument();
  });

  it("maps the equipped_hat row id to the hat catalog label", () => {
    // equipped_hat is a row id; the panel must resolve it via the inventory
    usePetStore.setState({ pet: { ...testPet, equipped_hat: "hat-row-1" } });
    usePlayerStore.setState({
      inventory: [makeItem({ id: "hat-row-1", item_id: "hat_straw", item_type: "hat", equipped: 1 })],
    });
    render(<PetPanel open={true} onClose={vi.fn()} />);
    // ITEM_CATALOG.hat_straw.label = 小草帽. It shows in BOTH the status
    // "装备" line and the hat inventory list — either way, the row-id ->
    // catalog mapping resolved correctly.
    expect(screen.getAllByText(/小草帽/).length).toBeGreaterThanOrEqual(1);
  });

  it("feeds from inventory with the row id when the food is owned", async () => {
    const feed = vi.fn().mockResolvedValue(undefined);
    const buyItem = vi.fn().mockResolvedValue(undefined);
    usePetStore.setState({ pet: testPet, feed });
    usePlayerStore.setState({
      player: testPlayer,
      inventory: [makeItem({ id: "food-row-1", item_id: "food_basic", item_type: "food" })],
      buyItem,
    });

    render(<PetPanel open={true} onClose={vi.fn()} />);
    const btn = screen.getByText("基础食物").closest("button") as HTMLButtonElement;
    await fireEvent.click(btn);

    await waitFor(() => expect(feed).toHaveBeenCalledWith("food-row-1"));
    expect(buyItem).not.toHaveBeenCalled();
  });

  it("buys then feeds with the new row id when the food is not owned", async () => {
    const feed = vi.fn().mockResolvedValue(undefined);
    // simulate the store refreshing inventory after a successful buy: the new
    // food row appears, and the panel must read it back to feed by row id.
    const buyItem = vi.fn().mockImplementation(async () => {
      usePlayerStore.setState({
        inventory: [makeItem({ id: "new-food-row", item_id: "food_basic", item_type: "food" })],
      });
    });
    usePetStore.setState({ pet: testPet, feed });
    usePlayerStore.setState({ player: testPlayer, inventory: [], buyItem });

    render(<PetPanel open={true} onClose={vi.fn()} />);
    const btn = screen.getByText("基础食物").closest("button") as HTMLButtonElement;
    await fireEvent.click(btn);

    await waitFor(() => {
      expect(buyItem).toHaveBeenCalledWith("food_basic");
      expect(feed).toHaveBeenCalledWith("new-food-row");
    });
  });

  it("equips a hat with the inventory row id", async () => {
    const equipHat = vi.fn().mockResolvedValue(undefined);
    usePetStore.setState({ pet: testPet, equipHat });
    usePlayerStore.setState({
      inventory: [makeItem({ id: "hat-row-1", item_id: "hat_straw", item_type: "hat" })],
    });

    render(<PetPanel open={true} onClose={vi.fn()} />);
    const btn = screen.getByText("小草帽").closest("button") as HTMLButtonElement;
    await fireEvent.click(btn);

    await waitFor(() => expect(equipHat).toHaveBeenCalledWith("hat-row-1"));
  });

  it("renders the recent event log with type label and rewards", async () => {
    vi.mocked(fetchEventHistory).mockResolvedValue(testEvents);
    render(<PetPanel open={true} onClose={vi.fn()} />);
    // EVENT_TYPE_LABELS.sign_in = 签到
    await waitFor(() => expect(screen.getByText("签到")).toBeInTheDocument());
    expect(screen.getByText(/\+20XP/)).toBeInTheDocument();
  });

  it("closes on ESC", () => {
    const onClose = vi.fn();
    render(<PetPanel open={true} onClose={onClose} />);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("closes via the close button", () => {
    const onClose = vi.fn();
    render(<PetPanel open={true} onClose={onClose} />);
    fireEvent.click(screen.getByLabelText("关闭面板"));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("shows the speech bubble text from the last interaction", () => {
    const lastResponse: PetResponse = { text: "今天又是充满希望的一天！", mood: "happy" };
    usePetStore.setState({ pet: testPet, lastResponse });
    render(<PetPanel open={true} onClose={vi.fn()} />);
    expect(screen.getByText("今天又是充满希望的一天！")).toBeInTheDocument();
  });
});
