import { useCallback, useEffect, useState } from "react";

import { listModels, listSlashItems } from "../../api/cc";
import type { ModelOption, SlashItem } from "../../api/cc";
import {
  listAgentModels,
  listAgentRuntimes,
  type AgentModel,
} from "../../api/agent";
import type { RuntimesState } from "./NewSessionDialog";

export function useSessionCatalogs(workdir: string) {
  const [slashItems, setSlashItems] = useState<readonly SlashItem[]>([]);
  const [models, setModels] = useState<readonly ModelOption[]>([]);
  const [codexModels, setCodexModels] = useState<readonly AgentModel[]>([]);
  const [codexCatalogError, setCodexCatalogError] = useState<string | null>(
    null,
  );
  const [runtimesState, setRuntimesState] = useState<RuntimesState>({
    status: "loading",
  });

  const loadRuntimes = useCallback(() => {
    setRuntimesState({ status: "loading" });
    listAgentRuntimes()
      .then((runtimes) =>
        setRuntimesState({ status: "ready", runtimes }),
      )
      .catch((error) =>
        setRuntimesState({
          status: "error",
          error: (error as Error).message,
        }),
      );
  }, []);

  useEffect(() => {
    listAgentRuntimes()
      .then((runtimes) =>
        setRuntimesState({ status: "ready", runtimes }),
      )
      .catch((error) =>
        setRuntimesState({
          status: "error",
          error: (error as Error).message,
        }),
      );
  }, []);

  const loadCodexModels = useCallback(() => {
    setCodexCatalogError(null);
    listAgentModels()
      .then(setCodexModels)
      .catch((error) => {
        setCodexModels([]);
        setCodexCatalogError((error as Error).message);
      });
  }, []);

  useEffect(() => {
    listAgentModels()
      .then(setCodexModels)
      .catch((error) => {
        setCodexModels([]);
        setCodexCatalogError((error as Error).message);
      });
  }, []);

  useEffect(() => {
    let cancelled = false;
    Promise.all([listSlashItems(workdir), listModels()])
      .then(([items, ccModels]) => {
        if (cancelled) return;
        setSlashItems(items);
        setModels(ccModels);
      })
      .catch(() => {
        if (cancelled) return;
        setSlashItems([]);
        setModels([]);
      });
    return () => {
      cancelled = true;
    };
  }, [workdir]);

  return {
    slashItems,
    models,
    codexModels,
    codexCatalogError,
    runtimesState,
    loadRuntimes,
    loadCodexModels,
  };
}
