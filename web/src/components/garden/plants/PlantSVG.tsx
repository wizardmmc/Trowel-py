interface PlantSVGProps {
  stage: "seed" | "sprout" | "tree" | "wilting";
  color?: string;
}

const CATEGORY_COLORS: Record<string, string> = {
  python: "#4A7C59",
  react: "#61DAFB",
  typescript: "#3178C6",
  rust: "#CE412B",
  go: "#00ADD8",
  default: "#8B7355",
};

export function getCategoryColor(category: string): string {
  const key = category.toLowerCase();
  for (const [k, v] of Object.entries(CATEGORY_COLORS)) {
    if (key.includes(k)) return v;
  }
  return CATEGORY_COLORS.default;
}

export function PlantSVG({ stage, color }: PlantSVGProps) {
  const c = color ?? "#4A7C59";

  switch (stage) {
    case "seed":
      return (
        <svg viewBox="0 0 80 80" width="80" height="80" fill="none">
          <circle cx="40" cy="52" r="8" fill={c} opacity="0.3" />
          <circle cx="40" cy="50" r="5" fill={c} />
          <line x1="40" y1="58" x2="36" y2="70" stroke={c} strokeWidth="1.5" strokeLinecap="round" />
          <line x1="40" y1="58" x2="44" y2="70" stroke={c} strokeWidth="1.5" strokeLinecap="round" />
        </svg>
      );
    case "sprout":
      return (
        <svg viewBox="0 0 80 80" width="80" height="80" fill="none">
          <line x1="40" y1="70" x2="40" y2="35" stroke={c} strokeWidth="2" strokeLinecap="round" />
          <ellipse cx="32" cy="38" rx="10" ry="6" fill={c} opacity="0.7" transform="rotate(-30 32 38)" />
          <ellipse cx="48" cy="42" rx="10" ry="6" fill={c} opacity="0.7" transform="rotate(30 48 42)" />
          <circle cx="40" cy="32" r="3" fill={c} />
        </svg>
      );
    case "tree":
      return (
        <svg viewBox="0 0 80 80" width="80" height="80" fill="none">
          <rect x="37" y="50" width="6" height="20" rx="2" fill="#8B7355" />
          <ellipse cx="40" cy="35" rx="22" ry="20" fill={c} opacity="0.6" />
          <ellipse cx="40" cy="35" rx="18" ry="16" fill={c} opacity="0.8" />
          <circle cx="34" cy="30" r="4" fill="#E8B84B" opacity="0.7" />
          <circle cx="48" cy="38" r="3" fill="#E8B84B" opacity="0.7" />
        </svg>
      );
    case "wilting":
      return (
        <svg viewBox="0 0 80 80" width="80" height="80" fill="none">
          <line x1="40" y1="70" x2="44" y2="35" stroke="#8B7355" strokeWidth="2" strokeLinecap="round" />
          <ellipse cx="52" cy="32" rx="12" ry="5" fill={c} opacity="0.4" transform="rotate(40 52 32)" />
          <ellipse cx="34" cy="38" rx="10" ry="4" fill={c} opacity="0.3" transform="rotate(-50 34 38)" />
          <line x1="44" y1="34" x2="40" y2="20" stroke="#8B7355" strokeWidth="1" strokeLinecap="round" opacity="0.4" />
        </svg>
      );
  }
}
