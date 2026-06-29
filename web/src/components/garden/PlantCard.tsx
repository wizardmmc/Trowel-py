import type { GardenPlant } from "../../api/client";
import { PlantSVG } from "./plants/PlantSVG";

interface PlantCardProps {
  readonly plant: GardenPlant;
  readonly onSelect: (id: string) => void;
}

export function PlantCard({ plant, onSelect }: PlantCardProps) {
  const isDue = plant.due !== null && new Date(plant.due) <= new Date();

  return (
    <button
      className={`plant-card ${isDue ? "plant-card--due" : ""}`}
      onClick={() => onSelect(plant.card_id)}
      aria-label={`${plant.title} - ${plant.plant_stage}`}
      data-testid={`plant-card-${plant.card_id}`}
    >
      <div className="plant-card__svg">
        <PlantSVG stage={plant.plant_stage} category={plant.category} />
      </div>
      <span className="plant-card__title">{plant.title}</span>
      {isDue && (
        <span className="plant-card__due-badge" aria-label="待复习">
          <svg className="plant-card__due-svg" viewBox="0 0 24 24" aria-hidden="true">
            <path d="M12 3s6 6.5 6 11a6 6 0 0 1-12 0c0-4.5 6-11 6-11z" />
          </svg>
        </span>
      )}
    </button>
  );
}
