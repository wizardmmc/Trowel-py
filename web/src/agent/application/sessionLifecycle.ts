/** 恢复 live session，并协调工作区历史与当前会话回放。 */

import { useEffect } from "react";

import { useAgentStore } from "./store";

const LIVE_SESSION_REFRESH_INTERVAL_MS = 5_000;

interface SessionLifecycleOptions {
  readonly workdir: string;
  readonly activeWorkdir: string | null;
  readonly activeConnected: boolean;
  readonly activeTurnCount: number;
  readonly activeRunning: boolean;
  readonly activeSid: string | null;
  readonly refreshHistory: (workdir: string) => Promise<void>;
  readonly loadHistoryIntoView: () => Promise<void>;
}

export function useSessionLifecycle({
  workdir,
  activeWorkdir,
  activeConnected,
  activeTurnCount,
  activeRunning,
  activeSid,
  refreshHistory,
  loadHistoryIntoView,
}: SessionLifecycleOptions): void {
  useEffect(() => {
    const refresh = () => {
      void useAgentStore.getState().refreshActiveSessions();
    };
    const refreshWhenVisible = () => {
      if (document.visibilityState === "visible") refresh();
    };
    refresh();
    const interval = window.setInterval(
      refreshWhenVisible,
      LIVE_SESSION_REFRESH_INTERVAL_MS,
    );
    window.addEventListener("focus", refresh);
    document.addEventListener("visibilitychange", refreshWhenVisible);
    return () => {
      window.clearInterval(interval);
      window.removeEventListener("focus", refresh);
      document.removeEventListener("visibilitychange", refreshWhenVisible);
    };
  }, []);

  useEffect(() => {
    if (workdir.trim()) void refreshHistory(workdir);
  }, [workdir, refreshHistory]);

  useEffect(() => {
    if (activeWorkdir) {
      void refreshHistory(activeWorkdir);
    }
  }, [activeWorkdir, refreshHistory]);

  useEffect(() => {
    if (
      activeSid &&
      activeConnected &&
      activeTurnCount === 0 &&
      !activeRunning
    ) {
      void loadHistoryIntoView();
    }
    // 回放只由 active sid 切换触发，避免流式字段更新重复加载。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeSid]);
}
