import { useEffect } from "react";

import { getAgentSessionDefaults } from "../transport/api";
import { useAgentStore } from "./store";

interface SessionLifecycleOptions {
  readonly workdir: string;
  readonly activeWorkdir: string | null;
  readonly activeConnected: boolean;
  readonly activeTurnCount: number;
  readonly activeHasAbort: boolean;
  readonly activeSid: string | null;
  readonly refreshHistory: (workdir: string) => Promise<void>;
  readonly loadHistoryIntoView: () => Promise<void>;
}

export function useSessionLifecycle({
  workdir,
  activeWorkdir,
  activeConnected,
  activeTurnCount,
  activeHasAbort,
  activeSid,
  refreshHistory,
  loadHistoryIntoView,
}: SessionLifecycleOptions): void {
  useEffect(() => {
    void (async () => {
      const store = useAgentStore.getState();
      await store.refreshActiveSessions();
      const current = useAgentStore.getState();
      const activeSession =
        current.sessions[current.activeSid ?? ""];
      if (
        !activeSession ||
        activeSession.sessionKind === "delegate" ||
        activeSession.workdir !== workdir
      ) {
        const defaults = await getAgentSessionDefaults().catch(() => null);
        await store.startSession({ workdir, ...defaults }).catch(() => {
          // mount 新建失败时保留空态，用户仍可手动重试。
        });
      }
      // 即使新建失败，也必须把历史下拉刷新到当前 workdir。
      void store.refreshHistory(workdir);
    })();
  }, [workdir]);

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
      !activeHasAbort
    ) {
      void loadHistoryIntoView();
    }
    // 回放只由 active sid 切换触发，避免流式字段更新重复加载。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeSid]);
}
