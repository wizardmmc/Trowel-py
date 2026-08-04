/** 把总览 store 状态、共享日期和复制操作接到纯展示组件。 */

import { useEffect, useRef, useState } from "react";
import { useStore } from "zustand";
import type { StoreApi } from "zustand/vanilla";
import { copyText } from "../../lib/copyText";
import { statisticsStore, type StatisticsState } from "../application/store";
import type { OverviewSessionProblem } from "../domain/overview";
import { OverviewStatisticsPanel } from "./OverviewStatisticsPanel";

export interface OverviewStatisticsViewProps {
  readonly store?: StoreApi<StatisticsState>;
}

export function OverviewStatisticsView({
  store = statisticsStore,
}: OverviewStatisticsViewProps) {
  const data = useStore(store, (state) => state.overview);
  const loading = useStore(store, (state) => state.overviewLoading);
  const error = useStore(store, (state) => state.overviewError);
  const dateRange = useStore(store, (state) => state.dateRange);
  const refreshOverview = useStore(store, (state) => state.refreshOverview);
  const [copiedProblemId, setCopiedProblemId] = useState<string | null>(null);
  const resetTimer = useRef<number | null>(null);

  useEffect(() => {
    void refreshOverview();
  }, [
    dateRange.startDate,
    dateRange.endDate,
    dateRange.timezone,
    refreshOverview,
  ]);

  useEffect(
    () => () => {
      if (resetTimer.current !== null) window.clearTimeout(resetTimer.current);
    },
    [],
  );

  const handleCopyProblem = async (
    problem: OverviewSessionProblem,
  ): Promise<void> => {
    try {
      await copyProblem(problem);
    } catch {
      return;
    }
    setCopiedProblemId(problem.trowel_session_id);
    if (resetTimer.current !== null) window.clearTimeout(resetTimer.current);
    resetTimer.current = window.setTimeout(
      () => setCopiedProblemId(null),
      1300,
    );
  };

  return (
    <OverviewStatisticsPanel
      data={data}
      loading={loading}
      error={error}
      copiedProblemId={copiedProblemId}
      onCopyProblem={(problem) => void handleCopyProblem(problem)}
    />
  );
}

/** 复制问题、Trowel session 与 runtime，不包含会话正文或原生身份。 */
async function copyProblem(problem: OverviewSessionProblem): Promise<void> {
  const text = [
    `问题：${problem.problem_text}`,
    `会话 ID：${problem.trowel_session_id}`,
    `Runtime：${problem.runtime}`,
  ].join("\n");
  await copyText(text);
}
