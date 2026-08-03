/** 组合 Memory 统计生产组件和仅供开发查看的页面外壳。 */

import { createStatisticsStore } from "../../statistics/application/store";
import { MemoryStatisticsView } from "../../statistics/ui/MemoryStatisticsView";
import "../statistics-preview.css";
import { memoryStatisticsPreviewData } from "./data";

const previewStore = createStatisticsStore({
  fetchMemory: async () => memoryStatisticsPreviewData,
});

const STATISTICS_SECTIONS = ["总览", "Agent", "Memory", "运行", "调用详情"] as const;

export function MemoryStatisticsPreview() {
  return (
    <main className="statistics-preview">
      <div className="statistics-preview__frame">
        <header className="statistics-preview__header">
          <div>
            <span>本机观察 · 2026-08-02</span>
            <h1>统计</h1>
          </div>
          <div className="statistics-preview__sample">脱敏样例</div>
        </header>
        <nav className="statistics-preview__tabs" aria-label="统计视图">
          {STATISTICS_SECTIONS.map((section) => (
            <span
              key={section}
              aria-current={section === "Memory" ? "page" : undefined}
            >
              {section}
            </span>
          ))}
        </nav>
        <div className="statistics-preview__content">
          <MemoryStatisticsView store={previewStore} />
        </div>
      </div>
    </main>
  );
}
