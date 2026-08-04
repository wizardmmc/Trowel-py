/** 纯 props 展示可用方向键切换的五个统计页签和共享日期范围。 */

import {
  useLayoutEffect,
  useRef,
  type KeyboardEvent,
  type ReactNode,
} from "react";
import type { StatisticsDateRange, StatisticsTab } from "../domain/types";
import { StatisticsDateRangePicker } from "./StatisticsDateRangePicker";
import "./statistics-tabs.css";

const TABS: readonly {
  readonly value: StatisticsTab;
  readonly label: string;
}[] = [
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
  const tabRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const panelRef = useRef<HTMLDivElement>(null);
  const previousTab = useRef(activeTab);

  useLayoutEffect(() => {
    const panel = panelRef.current;
    if (!panel || previousTab.current === activeTab) return;
    panel.scrollTop = 0;
    previousTab.current = activeTab;
  }, [activeTab]);

  const handleTabKeyDown = (
    event: KeyboardEvent<HTMLButtonElement>,
    index: number,
  ) => {
    const last = TABS.length - 1;
    const nextIndex =
      event.key === "ArrowRight"
        ? (index + 1) % TABS.length
        : event.key === "ArrowLeft"
          ? (index - 1 + TABS.length) % TABS.length
          : event.key === "Home"
            ? 0
            : event.key === "End"
              ? last
              : null;
    if (nextIndex === null) return;
    event.preventDefault();
    onTabChange(TABS[nextIndex].value);
    tabRefs.current[nextIndex]?.focus();
  };

  return (
    <section className="statistics-tabs">
      <header className="statistics-tabs__toolbar">
        <div role="tablist" aria-label="统计视图">
          {TABS.map((tab, index) => (
            <button
              key={tab.value}
              ref={(node) => {
                tabRefs.current[index] = node;
              }}
              id={`statistics-tab-${tab.value}`}
              type="button"
              role="tab"
              aria-controls={`statistics-panel-${tab.value}`}
              aria-selected={activeTab === tab.value}
              tabIndex={activeTab === tab.value ? 0 : -1}
              onClick={() => onTabChange(tab.value)}
              onKeyDown={(event) => handleTabKeyDown(event, index)}
            >
              {tab.label}
            </button>
          ))}
        </div>
        <div className="statistics-tabs__range">
          <StatisticsDateRangePicker
            value={dateRange}
            onChange={onDateRangeChange}
          />
        </div>
      </header>
      <div
        ref={panelRef}
        id={`statistics-panel-${activeTab}`}
        className="statistics-tabs__panel"
        role="tabpanel"
        aria-labelledby={`statistics-tab-${activeTab}`}
      >
        {children}
      </div>
    </section>
  );
}
