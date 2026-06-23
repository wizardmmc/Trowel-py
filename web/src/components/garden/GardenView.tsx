import { useEffect, useState, useRef } from "react";
import { useGardenStore } from "../../stores/gardenStore";
import { GardenGrid } from "./GardenGrid";
import { GardenStats } from "./GardenStats";
import { EmptyGarden } from "./EmptyGarden";
import { PlantDetailModal } from "./PlantDetailModal";
import { PetOverlay } from "../pet/PetOverlay";
import { PetPanel } from "../pet/PetPanel";
import { EventModal } from "../events/EventModal";
import { useEventStore } from "../../stores/eventStore";

interface GardenViewProps {
  readonly onStartReview: () => void;
}

export function GardenView({ onStartReview }: GardenViewProps) {
  const {
    plants,
    loading,
    error,
    sortBy,
    expandedCategories,
    selectedPlantId,
    stats,
    searchQuery,
    fetchGarden,
    searchPlants,
    clearSearch,
    setSortBy,
    toggleCategory,
    selectPlant,
  } = useGardenStore();

  const [searchInput, setSearchInput] = useState(searchQuery);
  const [panelOpen, setPanelOpen] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout>>(undefined);

  const { currentEvent, checkForEvent, claimReward } = useEventStore();

  useEffect(() => {
    fetchGarden();
  }, [fetchGarden]);

  // On entering the garden, ask the backend to run one event cycle. The engine
  // layer enforces cooldowns, so remounts won't spam-fire. Synchronous fetch —
  // see docs/training-log-m2.md slice 016 (no SSE).
  useEffect(() => {
    void checkForEvent();
  }, [checkForEvent]);

  useEffect(() => {
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, []);

  function handleSearchChange(value: string) {
    setSearchInput(value);
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => {
      searchPlants(value);
    }, 300);
  }

  const selectedPlant = selectedPlantId
    ? plants.find((p) => p.card_id === selectedPlantId) ?? null
    : null;

  if (error) {
    return <div className="garden-view__error">{error}</div>;
  }

  if (loading && plants.length === 0) {
    return <div className="garden-view__loading">Loading garden...</div>;
  }

  if (plants.length === 0 && !searchQuery.trim()) {
    return (
      <div className="garden-view">
        <EmptyGarden />
        <PetOverlay onClick={() => setPanelOpen(true)} />
        <PetPanel open={panelOpen} onClose={() => setPanelOpen(false)} />
        <EventModal event={currentEvent} onClaim={claimReward} />
      </div>
    );
  }

  return (
    <div className="garden-view" data-testid="garden-view">
      {/* Header: search + sort + review CTA */}
      <div className="garden-header">
        <input
          className="garden-header__search"
          type="text"
          placeholder="Search plants..."
          value={searchInput}
          onChange={(e) => handleSearchChange(e.target.value)}
          aria-label="Search plants"
          data-testid="garden-search"
        />
        <button
          className={`garden-header__sort-btn ${sortBy === "time" ? "garden-header__sort-btn--active" : ""}`}
          onClick={() => setSortBy(sortBy === "category" ? "time" : "category")}
          title={sortBy === "category" ? "Sort by time" : "Sort by category"}
          aria-label={`Sort by ${sortBy === "category" ? "time" : "category"}`}
        >
          {sortBy === "category" ? "\u{1F4C1}" : "\u{1F552}"}
        </button>
        {(stats?.due_count ?? 0) > 0 && (
          <button
            className="btn btn--primary"
            onClick={onStartReview}
            data-testid="garden-review-cta"
          >
            Start Review ({stats?.due_count ?? 0})
          </button>
        )}
      </div>

      <GardenStats stats={stats} plants={plants} />

      {plants.length === 0 && searchQuery.trim() && (
        <div className="garden-view__empty-search">
          <p>No plants matching &ldquo;{searchQuery}&rdquo;</p>
          <button className="garden-view__clear-search" onClick={clearSearch}>
            Clear search
          </button>
        </div>
      )}

      {plants.length > 0 && (
        <GardenGrid
          plants={plants}
          sortBy={sortBy}
          expandedCategories={expandedCategories}
          onToggleCategory={toggleCategory}
          onSelectPlant={selectPlant}
        />
      )}

      <PlantDetailModal plant={selectedPlant} onClose={() => selectPlant(null)} />
      <PetOverlay onClick={() => setPanelOpen(true)} />
      <PetPanel open={panelOpen} onClose={() => setPanelOpen(false)} />
      <EventModal event={currentEvent} onClaim={claimReward} />
    </div>
  );
}
