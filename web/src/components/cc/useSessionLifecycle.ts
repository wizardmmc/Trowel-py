import { useEffect } from "react";

import {
  useCcStore,
  type PerSessionState,
} from "../../stores/ccStore";

interface SessionLifecycleOptions {
  readonly workdir: string;
  readonly active: PerSessionState | null;
  readonly activeSid: string | null;
  readonly refreshHistory: (workdir: string) => Promise<void>;
  readonly loadHistoryIntoView: () => Promise<void>;
}

export function useSessionLifecycle({
  workdir,
  active,
  activeSid,
  refreshHistory,
  loadHistoryIntoView,
}: SessionLifecycleOptions): void {
  useEffect(() => {
    void (async () => {
      const store = useCcStore.getState();
      await store.refreshActiveSessions();
      const current = useCcStore.getState();
      const activeSession =
        current.sessions[current.activeSid ?? ""];
      if (!activeSession || activeSession.workdir !== workdir) {
        await store.startSession({ workdir }).catch(() => {
          // mount 新建失败时保留空态，用户仍可手动重试。
        });
      }
      // 即使新建失败，也必须把历史下拉刷新到当前 workdir。
      void store.refreshHistory(workdir);
    })();
  }, [workdir]);

  useEffect(() => {
    if (active?.workdir) {
      void refreshHistory(active.workdir);
    }
  }, [active?.workdir, refreshHistory]);

  useEffect(() => {
    if (
      active &&
      active.connected &&
      active.turns.length === 0 &&
      !active.abort
    ) {
      void loadHistoryIntoView();
    }
    // 回放只由 active sid 切换触发，避免流式字段更新重复加载。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeSid]);
}
