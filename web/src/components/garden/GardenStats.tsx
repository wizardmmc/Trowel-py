import { useMemo } from "react";
import type { GardenPlant, GardenStatsData } from "../../api/client";

interface GardenStatsProps {
  readonly stats: GardenStatsData | null;
  readonly plants?: readonly GardenPlant[];
}

const STAGE_LABELS: Record<string, string> = {
  seed: "种子",
  sprout: "发芽",
  tree: "成熟",
  wilting: "枯萎",
};

const STAGE_ORDER = ["seed", "sprout", "tree", "wilting"];

export function GardenStats({ stats, plants }: GardenStatsProps) {
  const cardsByStage = useMemo(() => {
    if (!plants) return { seed: 0, sprout: 0, tree: 0, wilting: 0 };
    const counts: Record<string, number> = { seed: 0, sprout: 0, tree: 0, wilting: 0 };
    for (const p of plants) {
      counts[p.plant_stage] = (counts[p.plant_stage] ?? 0) + 1;
    }
    return counts;
  }, [plants]);

  if (!stats) return null;

  return (
    <div className="garden-stats" data-testid="garden-stats">
      <div className="garden-stats__item">
        <span className="garden-stats__value">{stats.total_plants}</span>
        <span className="garden-stats__label">植物</span>
      </div>
      <div className="garden-stats__item">
        <span className="garden-stats__value garden-stats__value--accent">
          {stats.due_count}
        </span>
        <span className="garden-stats__label">待复习</span>
      </div>
      <div className="garden-stats__item">
        <span className="garden-stats__value">
          {stats.flowering_rate.toFixed(0)}%
        </span>
        <span className="garden-stats__label">绽放率</span>
      </div>
      <div className="garden-stats__divider" />
      {STAGE_ORDER.map((stage) => (
        <div key={stage} className="garden-stats__item">
          <span className="garden-stats__value garden-stats__value--stage">
            {cardsByStage[stage] ?? 0}
          </span>
          <span className="garden-stats__label">{STAGE_LABELS[stage]}</span>
        </div>
      ))}
    </div>
  );
}
