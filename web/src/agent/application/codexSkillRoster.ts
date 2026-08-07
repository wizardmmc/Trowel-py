/** 按当前 Codex 会话加载连接私有且工作目录相关的技能清单。 */

import { useCallback, useEffect, useState } from "react";

import { listCodexSkills, type CodexSkill } from "../transport";

interface SkillRosterState {
  readonly requestKey: string | null;
  readonly skills: readonly CodexSkill[];
  readonly warnings: readonly string[];
  readonly loading: boolean;
  readonly error: string | null;
}

const EMPTY_STATE: SkillRosterState = {
  requestKey: null,
  skills: [],
  warnings: [],
  loading: false,
  error: null,
};

/** 绑定 session ID 加载技能，切换会话时不短暂展示上一连接的数据。 */
export function useCodexSkillRoster(
  sessionId: string | null,
  enabled: boolean,
) {
  const [state, setState] = useState<SkillRosterState>(EMPTY_STATE);
  const [generation, setGeneration] = useState(0);

  useEffect(() => {
    if (!sessionId || !enabled) return;
    const controller = new AbortController();
    const requestKey = `${sessionId}:${generation}`;
    void listCodexSkills(sessionId, controller.signal)
      .then((catalog) => {
        if (!controller.signal.aborted) {
          setState({
            requestKey,
            skills: catalog.skills,
            warnings: catalog.errors,
            loading: false,
            error: null,
          });
        }
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        setState({
          requestKey,
          skills: [],
          warnings: [],
          loading: false,
          error: error instanceof Error ? error.message : String(error),
        });
      });
    return () => controller.abort();
  }, [sessionId, enabled, generation]);

  const retry = useCallback(() => setGeneration((value) => value + 1), []);
  if (!sessionId || !enabled) return { ...EMPTY_STATE, retry };
  const requestKey = `${sessionId}:${generation}`;
  if (state.requestKey !== requestKey) {
    return { ...EMPTY_STATE, requestKey, loading: true, retry };
  }
  return { ...state, retry };
}
