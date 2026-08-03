/** 纯 props 展示五个统计页签和共享日期范围控件。 */

import type { ReactNode } from "react";
import type { StatisticsDateRange, StatisticsTab } from "../domain/types";

const TABS: readonly { readonly value: StatisticsTab; readonly label: string }[] = [
  { value: "overview", label: "总览" },
  { value: "agent", label: "Agent" },
  { value: "memory", label: "Memory" },
  { value: "runtime", label: "运行" },
  { value: "calls", label: "调用详情" },
];

export interface StatisticsTabsProps {
  readonly activeTab: StatisticsTab;
  readonly dateRange: StatisticsDateRange;
  readonly onTabChange: (tab: StatisticsTab) => void;
  readonly onDateRangeChange: (range: StatisticsDateRange) => void;
  readonly children: ReactNode;
}

export function StatisticsTabs({
  activeTab,
  dateRange,
  onTabChange,
  onDateRangeChange,
  children,
}: StatisticsTabsProps) {
  return (
    <section className="statistics-tabs">
      <header className="statistics-tabs__toolbar">
        <div role="tablist" aria-label="统计视图">
          {TABS.map((tab) => (
            <button
              key={tab.value}
              type="button"
              role="tab"
              aria-selected={activeTab === tab.value}
              onClick={() => onTabChange(tab.value)}
            >
              {tab.label}
            </button>
          ))}
        </div>
        <div className="statistics-tabs__dates">
          <label>
            <span>开始日期</span>
            <input
              aria-label="开始日期"
              type="date"
              value={dateRange.startDate}
              onChange={(event) =>
                onDateRangeChange({
                  ...dateRange,
                  startDate: event.currentTarget.value,
                })
              }
            />
          </label>
          <label>
            <span>结束日期</span>
            <input
              aria-label="结束日期"
              type="date"
              value={dateRange.endDate}
              onChange={(event) =>
                onDateRangeChange({
                  ...dateRange,
                  endDate: event.currentTarget.value,
                })
              }
            />
          </label>
        </div>
      </header>
      <div role="tabpanel">{children}</div>
    </section>
  );
}
