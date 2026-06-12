import { useMemo } from "react";
import { motion, AnimatePresence } from "framer-motion";
import type { GardenPlant } from "../../api/client";
import type { SortMode } from "../../stores/gardenStore";
import { PlantCard } from "./PlantCard";

interface GardenGridProps {
  readonly plants: readonly GardenPlant[];
  readonly sortBy: SortMode;
  readonly expandedCategories: ReadonlySet<string>;
  readonly onToggleCategory: (cat: string) => void;
  readonly onSelectPlant: (id: string) => void;
}

const COLLAPSE_THRESHOLD = 50;

const containerVariants = {
  hidden: {},
  visible: { transition: { staggerChildren: 0.05 } },
};

const itemVariants = {
  hidden: { scale: 0, opacity: 0 },
  visible: { scale: 1, opacity: 1, transition: { duration: 0.3, ease: "easeOut" as const } },
};

export function GardenGrid({
  plants,
  sortBy,
  expandedCategories,
  onToggleCategory,
  onSelectPlant,
}: GardenGridProps) {
  const grouped = useMemo(() => {
    if (sortBy === "time") return null;
    const map = new Map<string, GardenPlant[]>();
    for (const plant of plants) {
      const existing = map.get(plant.category) ?? [];
      existing.push(plant);
      map.set(plant.category, existing);
    }
    return map;
  }, [plants, sortBy]);

  const shouldCollapse = plants.length > COLLAPSE_THRESHOLD;

  if (sortBy === "time") {
    return (
      <motion.div
        className="garden-grid garden-grid--flat"
        variants={containerVariants}
        initial="hidden"
        animate="visible"
        data-testid="garden-grid"
      >
        {plants.map((plant) => (
          <motion.div key={plant.card_id} variants={itemVariants}>
            <PlantCard plant={plant} onSelect={onSelectPlant} />
          </motion.div>
        ))}
      </motion.div>
    );
  }

  if (!grouped) return null;

  const categories = [...grouped.keys()];

  return (
    <div className="garden-grid" data-testid="garden-grid">
      {categories.map((category) => {
        const categoryPlants = grouped.get(category) ?? [];
        const isExpanded = !shouldCollapse || expandedCategories.has(category);

        return (
          <div key={category} className="garden-grid__category">
            <button
              className="garden-grid__category-header"
              onClick={() => shouldCollapse && onToggleCategory(category)}
              aria-expanded={isExpanded}
            >
              <span className={`garden-grid__category-expand ${isExpanded ? "garden-grid__category-expand--open" : ""}`}>
                ▶
              </span>
              {category}
              <span className="garden-grid__category-count">({categoryPlants.length})</span>
            </button>
            <AnimatePresence initial={false}>
              {isExpanded && (
                <motion.div
                  className="garden-grid__plants"
                  initial={{ height: 0, opacity: 0 }}
                  animate={{ height: "auto", opacity: 1 }}
                  exit={{ height: 0, opacity: 0 }}
                  transition={{ duration: 0.2 }}
                  variants={containerVariants}
                  style={{ overflow: "hidden" }}
                >
                  {categoryPlants.map((plant) => (
                    <motion.div key={plant.card_id} variants={itemVariants}>
                      <PlantCard plant={plant} onSelect={onSelectPlant} />
                    </motion.div>
                  ))}
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        );
      })}
    </div>
  );
}
