/** 按当前 Codex 会话加载原生命令清单，并处理请求取消和重试。 */

import { useCallback, useEffect, useState } from "react";

import {
  listCodexCommands,
  type CodexCommand,
} from "../transport";

interface RosterState {
  readonly requestKey: string | null;
  readonly commands: readonly CodexCommand[];
  readonly loading: boolean;
  readonly error: string | null;
}

const EMPTY_STATE: RosterState = {
  requestKey: null,
  commands: [],
  loading: false,
  error: null,
};

export function useCodexCommandRoster(
  sessionId: string | null,
  enabled: boolean,
) {
  const [state, setState] = useState<RosterState>(EMPTY_STATE);
  const [generation, setGeneration] = useState(0);

  useEffect(() => {
    if (!sessionId || !enabled) return;
    const controller = new AbortController();
    const requestKey = `${sessionId}:${generation}`;
    void listCodexCommands(sessionId, controller.signal)
      .then((commands) => {
        if (!controller.signal.aborted) {
          setState({ requestKey, commands, loading: false, error: null });
        }
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        setState({
          requestKey,
          commands: [],
          loading: false,
          error: error instanceof Error ? error.message : String(error),
        });
      });
    return () => controller.abort();
  }, [sessionId, enabled, generation]);

  const retry = useCallback(() => setGeneration((value) => value + 1), []);
  if (!sessionId || !enabled) {
    return { ...EMPTY_STATE, retry };
  }
  const requestKey = `${sessionId}:${generation}`;
  if (state.requestKey !== requestKey) {
    return { requestKey, commands: [], loading: true, error: null, retry };
  }
  return { ...state, retry };
}
