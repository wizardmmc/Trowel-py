import type { GardenPlant } from "../../api/client";
import { PlantSVG, getCategoryColor } from "./plants/PlantSVG";

interface PlantDetailModalProps {
  readonly plant: GardenPlant | null;
  readonly onClose: () => void;
}

const STAGE_LABELS: Record<string, string> = {
  seed: "🌱 Seed",
  sprout: "🌿 Sprout",
  tree: "🌳 Tree",
  wilting: "🥀 Wilting",
};

export function PlantDetailModal({ plant, onClose }: PlantDetailModalProps) {
  if (!plant) return null;

  const isDue = plant.due !== null && new Date(plant.due) <= new Date();
  const color = getCategoryColor(plant.category);

  return (
    <div className="plant-modal__overlay" onClick={onClose}>
      <div
        className="plant-modal__content"
        onClick={(e) => e.stopPropagation()}
        data-testid="plant-detail-modal"
      >
        <button className="plant-modal__close" onClick={onClose}>✕</button>

        <div className="plant-modal__plant">
          <PlantSVG stage={plant.plant_stage} color={color} />
        </div>

        <h2 className="plant-modal__title">{plant.title}</h2>
        <span className="plant-modal__category">{plant.category}</span>
        <span className="plant-modal__stage">{STAGE_LABELS[plant.plant_stage] ?? plant.plant_stage}</span>

        {isDue && <p className="plant-modal__due">💧 Needs watering</p>}

        <div className="plant-modal__explanation">
          <p>{plant.explanation}</p>
        </div>

        <div className="plant-modal__meta">
          <span>Reviews: {plant.reps}</span>
        </div>
      </div>
    </div>
  );
}
