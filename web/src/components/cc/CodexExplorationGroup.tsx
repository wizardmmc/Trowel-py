import type { ToolItem } from "../../stores/ccStore";
import { ToolBlock } from "./ToolBlock";

interface CodexExplorationGroupProps {
  readonly items: readonly ToolItem[];
  readonly workdir?: string;
}

export function CodexExplorationGroup({ items, workdir }: CodexExplorationGroupProps) {
  const running = items.some((item) => item.status === "running");
  const failed = items.filter((item) => item.status === "failed").length;
  const label = `${running ? "Exploring" : "Explored"}${failed > 0 ? ` · ${failed} failed` : ""}`;
  return (
    <section className="cc-exploration" aria-label={label}>
      <div className="cc-exploration__head">
        <span className="cc-exploration__dot" data-state={failed > 0 ? "failed" : running ? "running" : "done"} aria-hidden="true" />
        <span className="cc-exploration__title">{label}</span>
      </div>
      <div className="cc-exploration__items">
        {items.map((item) => (
          <ToolBlock key={item.toolUseId} item={item} workdir={workdir} codexExploration />
        ))}
      </div>
    </section>
  );
}
