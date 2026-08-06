/** 加载新会话需要的 runtime、模型和斜杠命令清单。 */

import { useCallback, useEffect, useState } from "react";

import { listModels, listSlashItems } from "../../api/cc";
import type { ModelOption, SlashItem } from "../../api/cc";
import {
  listAgentModels,
  listAgentConnectionOptions,
  listAgentRuntimes,
  type AgentConnectionOption,
  type AgentModel,
} from "../../agent/transport";
import type { RuntimesState } from "./NewSessionDialog";

export function useSessionCatalogs(workdir: string) {
  const [slashCatalog, setSlashCatalog] = useState<{
    readonly workdir: string;
    readonly items: readonly SlashItem[];
  }>({ workdir: "", items: [] });
  const slashItems =
    workdir.trim() && slashCatalog.workdir === workdir
      ? slashCatalog.items
      : [];
  const [models, setModels] = useState<readonly ModelOption[]>([]);
  const [codexModels, setCodexModels] = useState<readonly AgentModel[]>([]);
  const [codexCatalogError, setCodexCatalogError] = useState<string | null>(
    null,
  );
  const [runtimesState, setRuntimesState] = useState<RuntimesState>({
    status: "loading",
  });
  const [connectionOptions, setConnectionOptions] = useState<
    readonly AgentConnectionOption[]
  >([]);
  const [connectionOptionsLoading, setConnectionOptionsLoading] = useState(true);
  const [connectionOptionsError, setConnectionOptionsError] = useState<
    string | null
  >(null);

  const loadConnectionOptions = useCallback(async () => {
    setConnectionOptionsLoading(true);
    setConnectionOptionsError(null);
    try {
      const options = await listAgentConnectionOptions();
      setConnectionOptions(options);
      setConnectionOptionsError(null);
    } catch (error) {
      setConnectionOptions([]);
      setConnectionOptionsError((error as Error).message);
    } finally {
      setConnectionOptionsLoading(false);
    }
  }, []);

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
    listModels()
      .then(setModels)
      .catch(() => setModels([]));
  }, []);

  useEffect(() => {
    if (!workdir.trim()) {
      return;
    }
    let cancelled = false;
    listSlashItems(workdir)
      .then((items) => {
        if (cancelled) return;
        setSlashCatalog({ workdir, items });
      })
      .catch(() => {
        if (cancelled) return;
        setSlashCatalog({ workdir, items: [] });
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
    connectionOptions,
    connectionOptionsLoading,
    connectionOptionsError,
    loadRuntimes,
    loadConnectionOptions,
    loadCodexModels,
  };
}
