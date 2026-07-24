import { getCategoryColor } from "./categoryColors";

export type PlantStage = "seed" | "sprout" | "tree" | "wilting";

interface PlantSVGProps {
  readonly stage: PlantStage;
  readonly category: string;
}

export function PlantSVG({ stage, category }: PlantSVGProps) {
  const color = getCategoryColor(category);
  switch (stage) {
    case "seed":
      return <SeedSVG color={color} />;
    case "sprout":
      return <SproutSVG color={color} />;
    case "tree":
      return <TreeSVG color={color} />;
    case "wilting":
      return <WiltingSVG color={color} />;
  }
}

interface StageProps {
  readonly color: string;
}

function SeedSVG({ color }: StageProps) {
  return (
    <svg
      width="48"
      height="48"
      viewBox="0 0 48 48"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
    >
      <ellipse cx="24" cy="28" rx="6" ry="8" stroke={color} strokeWidth="2" fill="none" />
      <line x1="21" y1="36" x2="18" y2="43" stroke={color} strokeWidth="1.5" strokeLinecap="round" />
      <line x1="27" y1="36" x2="30" y2="43" stroke={color} strokeWidth="1.5" strokeLinecap="round" />
      <line x1="24" y1="22" x2="24" y2="34" stroke={color} strokeWidth="1" opacity="0.5" />
    </svg>
  );
}

function SproutSVG({ color }: StageProps) {
  return (
    <svg
      width="48"
      height="48"
      viewBox="0 0 48 48"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
    >
      <line x1="24" y1="38" x2="24" y2="18" stroke={color} strokeWidth="2" strokeLinecap="round" />
      <path
        d="M24 24 C20 22, 14 18, 14 14 C14 12, 16 12, 18 14 C20 16, 22 20, 24 24"
        stroke={color}
        strokeWidth="1.5"
        fill="none"
      />
      <path
        d="M24 20 C28 18, 34 14, 34 10 C34 8, 32 8, 30 10 C28 12, 26 16, 24 20"
        stroke={color}
        strokeWidth="1.5"
        fill="none"
      />
    </svg>
  );
}

function TreeSVG({ color }: StageProps) {
  return (
    <svg
      width="48"
      height="48"
      viewBox="0 0 48 48"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
    >
      <line x1="24" y1="40" x2="24" y2="20" stroke={color} strokeWidth="3" strokeLinecap="round" />
      <line x1="24" y1="28" x2="16" y2="22" stroke={color} strokeWidth="2" strokeLinecap="round" />
      <line x1="24" y1="24" x2="32" y2="18" stroke={color} strokeWidth="2" strokeLinecap="round" />
      <circle cx="24" cy="14" r="10" stroke={color} strokeWidth="1.5" fill="none" />
      <path d="M16 16 C16 8, 24 6, 24 6" stroke={color} strokeWidth="1" fill="none" opacity="0.6" />
      <path d="M32 16 C32 8, 24 6, 24 6" stroke={color} strokeWidth="1" fill="none" opacity="0.6" />
      <circle cx="18" cy="12" r="1.5" fill={color} opacity="0.4" />
      <circle cx="30" cy="14" r="1.5" fill={color} opacity="0.4" />
      <circle cx="24" cy="8" r="1" fill={color} opacity="0.3" />
    </svg>
  );
}

function WiltingSVG({ color }: StageProps) {
  return (
    <svg
      width="48"
      height="48"
      viewBox="0 0 48 48"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
    >
      <line x1="24" y1="40" x2="20" y2="18" stroke={color} strokeWidth="2" strokeLinecap="round" />
      <path
        d="M22 26 C18 28, 12 26, 10 30 C10 32, 12 32, 14 30 C16 28, 20 28, 22 26"
        stroke={color}
        strokeWidth="1.5"
        fill="none"
      />
      <path
        d="M21 22 C25 20, 32 18, 34 22 C34 24, 32 24, 30 22 C28 20, 24 22, 21 22"
        stroke={color}
        strokeWidth="1.5"
        fill="none"
      />
      <path d="M28 34 C29 33, 30 34, 29 35" stroke={color} strokeWidth="1" fill="none" opacity="0.5" />
    </svg>
  );
}
