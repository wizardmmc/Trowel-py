/** Map category names to accent colors for plant tinting. Mirrors TS. */

const CATEGORY_COLORS: Record<string, string> = {
  javascript: "#4A7C59",
  typescript: "#3178C6",
  react: "#61DAFB",
  node: "#68A063",
  python: "#3776AB",
  database: "#5B8FA8",
  sql: "#5B8FA8",
  security: "#C0392B",
  design: "#A87C5B",
  algorithms: "#8E44AD",
  performance: "#E67E22",
  testing: "#27AE60",
  devops: "#2C3E50",
  architecture: "#7F8C8D",
};

/** Match a category against known keywords (substring, case-insensitive),
 *  falling back to a default earth-green. */
export function getCategoryColor(category: string): string {
  const key = category.toLowerCase();
  for (const [k, v] of Object.entries(CATEGORY_COLORS)) {
    if (key.includes(k)) return v;
  }
  return "#5D8A5E"; // default earth-green
}
