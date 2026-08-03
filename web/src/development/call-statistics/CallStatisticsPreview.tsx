/** 组合调用详情生产组件和仅供开发查看的真实数据页面外壳。 */

import { CallStatisticsView } from "../../statistics/ui/CallStatisticsView";
import "../statistics-preview.css";

const STATISTICS_SECTIONS = [
  "总览",
  "Agent",
  "Memory",
  "运行",
  "调用详情",
] as const;

/** 在独立开发页面中复用生产调用详情组件和真实 Statistics API。 */
export function CallStatisticsPreview() {
  return (
    <main className="statistics-preview">
      <div className="statistics-preview__frame">
        <header className="statistics-preview__header">
          <div>
            <span>本机观察</span>
            <h1>统计</h1>
          </div>
          <div className="statistics-preview__sample">真实本机数据</div>
        </header>
        <nav className="statistics-preview__tabs" aria-label="统计视图">
          {STATISTICS_SECTIONS.map((section) => (
            <span
              key={section}
              aria-current={section === "调用详情" ? "page" : undefined}
            >
              {section}
            </span>
          ))}
        </nav>
        <div className="statistics-preview__content">
          <CallStatisticsView />
        </div>
      </div>
    </main>
  );
}
