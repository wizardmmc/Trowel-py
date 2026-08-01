import { useCallback, useEffect, useState } from "react";

import {
  listCodexCommands,
  type CodexCommand,
  type Runtime,
} from "../../agent/transport";

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
  runtime: Runtime | null,
) {
  const [state, setState] = useState<RosterState>(EMPTY_STATE);
  const [generation, setGeneration] = useState(0);

  useEffect(() => {
    if (!sessionId || runtime !== "codex") return;
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
  }, [sessionId, runtime, generation]);

  const retry = useCallback(() => setGeneration((value) => value + 1), []);
  if (!sessionId || runtime !== "codex") {
    return { ...EMPTY_STATE, retry };
  }
  const requestKey = `${sessionId}:${generation}`;
  if (state.requestKey !== requestKey) {
    return { requestKey, commands: [], loading: true, error: null, retry };
  }
  return { ...state, retry };
}
