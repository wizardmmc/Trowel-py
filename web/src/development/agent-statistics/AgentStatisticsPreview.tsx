/** 组合 Agent 统计生产组件和仅供开发查看的页面外壳。 */

import { createStatisticsStore } from "../../statistics/application/store";
import { AgentStatisticsView } from "../../statistics/ui/AgentStatisticsView";
import { agentStatisticsPreviewData } from "./data";
import "../statistics-preview.css";

const previewStore = createStatisticsStore({
  fetchAgent: async () => agentStatisticsPreviewData,
});

const STATISTICS_SECTIONS = ["总览", "Agent", "Memory", "运行", "调用详情"] as const;

export function AgentStatisticsPreview() {
  return (
    <main className="statistics-preview">
      <div className="statistics-preview__frame">
        <header className="statistics-preview__header">
          <div>
            <span>本机观察</span>
            <h1>统计</h1>
          </div>
          <div className="statistics-preview__sample">脱敏样例</div>
        </header>
        <nav className="statistics-preview__tabs" aria-label="统计视图">
          {STATISTICS_SECTIONS.map((section) => (
            <span
              key={section}
              aria-current={section === "Agent" ? "page" : undefined}
            >
              {section}
            </span>
          ))}
        </nav>
        <div className="statistics-preview__content">
          <AgentStatisticsView store={previewStore} />
        </div>
      </div>
    </main>
  );
}
