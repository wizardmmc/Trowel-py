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
    return <div className="garden-view__loading">正在加载花园…</div>;
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
          placeholder="搜索植物…"
          value={searchInput}
          onChange={(e) => handleSearchChange(e.target.value)}
          aria-label="搜索植物"
          data-testid="garden-search"
        />
        <button
          className={`garden-header__sort-btn ${sortBy === "time" ? "garden-header__sort-btn--active" : ""}`}
          onClick={() => setSortBy(sortBy === "category" ? "time" : "category")}
          title={sortBy === "category" ? "按时间排序" : "按分类排序"}
          aria-label={`排序方式：${sortBy === "category" ? "时间" : "分类"}`}
        >
          {sortBy === "category" ? (
            <svg className="garden-header__sort-svg" viewBox="0 0 24 24" aria-hidden="true">
              <path d="M3 7a1 1 0 0 1 1-1h5l2 2h8a1 1 0 0 1 1 1v8a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1z" />
            </svg>
          ) : (
            <svg className="garden-header__sort-svg" viewBox="0 0 24 24" aria-hidden="true">
              <circle cx="12" cy="12" r="9" />
              <path d="M12 7v5l3 2" />
            </svg>
          )}
        </button>
        {(stats?.due_count ?? 0) > 0 && (
          <button
            className="btn btn--primary"
            onClick={onStartReview}
            data-testid="garden-review-cta"
          >
            开始复习（{stats?.due_count ?? 0}）
          </button>
        )}
      </div>

      <GardenStats stats={stats} plants={plants} />

      {plants.length === 0 && searchQuery.trim() && (
        <div className="garden-view__empty-search">
          <p>没有匹配「{searchQuery}」的植物</p>
          <button className="garden-view__clear-search" onClick={clearSearch}>
            清除搜索
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
