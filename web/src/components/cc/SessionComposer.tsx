import type { ModelOption, SlashItem } from "../../api/cc";
import type { AgentModel } from "../../api/agent";
import type { PerSessionState } from "../../stores/ccStore";
import { Composer } from "./Composer";

interface SessionComposerProps {
  readonly active: PerSessionState | null;
  readonly activeSid: string | null;
  readonly streaming: boolean;
  readonly slashItems: readonly SlashItem[];
  readonly ccModels: readonly ModelOption[];
  readonly codexModels: readonly AgentModel[];
  readonly codexCatalogError: string | null;
  readonly onRetryCodexCatalog: () => void;
  readonly onSend: (text: string) => void;
  readonly onInterrupt: () => void;
  readonly onUpdateSettings: (model: string, effort: string) => void;
  readonly onRequestModelPicker: () => void;
  readonly onRequestEffortPicker: () => void;
}

export function SessionComposer({
  active,
  activeSid,
  streaming,
  slashItems,
  ccModels,
  codexModels,
  codexCatalogError,
  onRetryCodexCatalog,
  onSend,
  onInterrupt,
  onUpdateSettings,
  onRequestModelPicker,
  onRequestEffortPicker,
}: SessionComposerProps) {
  const phase = active?.phase ?? "idle";
  const effort = active?.effort ?? null;
  const meta = active?.meta ?? null;
  const ccControls = active?.runtime === "claude_code";
  const codexControls = active?.runtime === "codex";
  const modelAlias = (() => {
    if (!meta?.model) return null;
    const found = ccModels.find(
      (model) =>
        model.value === meta.model || model.real_model === meta.model,
    );
    return found?.value ?? null;
  })();
  const codexModelOptions: readonly ModelOption[] = codexModels.map(
    (model) => ({
      value: model.id,
      label: model.display_name,
      real_model: model.model,
      description: model.description,
      is_default: model.is_default,
    }),
  );
  const codexCurrentModel =
    active?.pendingModel ?? meta?.model ?? null;
  const selectedCodexModel =
    codexModels.find(
      (model) =>
        model.id === codexCurrentModel ||
        model.model === codexCurrentModel,
    ) ??
    codexModels.find((model) => model.is_default) ??
    codexModels[0];
  const codexEfforts = (selectedCodexModel?.supported_efforts ?? []).map(
    (option) => ({
      value: option.value,
      description: option.description,
      isDefault:
        option.value === selectedCodexModel?.default_effort,
    }),
  );
  const codexCurrentEffort = active?.pendingEffort ?? effort;

  function pickCodexModel(modelId: string): void {
    const next = codexModels.find((model) => model.id === modelId);
    if (!next) return;
    const nextEffort = next.supported_efforts.some(
      (option) => option.value === codexCurrentEffort,
    )
      ? (codexCurrentEffort as string)
      : next.default_effort;
    onUpdateSettings(next.id, nextEffort);
  }

  return (
    <Composer
      streaming={streaming}
      disabled={!activeSid || phase === "awaiting_input"}
      awaitingInput={phase === "awaiting_input"}
      onSend={onSend}
      onInterrupt={onInterrupt}
      slashItems={ccControls ? slashItems : []}
      models={
        ccControls
          ? ccModels
          : codexControls
            ? codexModelOptions
            : []
      }
      efforts={codexControls ? codexEfforts : undefined}
      currentModelAlias={
        ccControls
          ? modelAlias
          : codexControls
            ? codexCurrentModel
            : null
      }
      currentEffort={
        ccControls
          ? effort
          : codexControls
            ? codexCurrentEffort
            : null
      }
      onPickModel={
        ccControls
          ? (value) => onSend(`/model ${value}`)
          : codexControls
            ? pickCodexModel
            : undefined
      }
      onPickEffort={
        ccControls
          ? (value) => onSend(`/effort ${value}`)
          : codexControls
            ? (value) => {
                if (selectedCodexModel) {
                  onUpdateSettings(selectedCodexModel.id, value);
                }
              }
            : undefined
      }
      modelCatalogError={codexControls ? codexCatalogError : null}
      onRetryModelCatalog={
        codexControls ? onRetryCodexCatalog : undefined
      }
      settingsDisabled={streaming}
      permissionFacts={
        codexControls && active
          ? {
              requested: active.permissionPreset ?? null,
              profile: active.effectivePermissionProfile ?? null,
              sandbox: active.effectiveSandbox ?? null,
              approval: active.effectiveApproval ?? null,
              network: active.networkAccess ?? null,
              label: active.permission,
            }
          : null
      }
      onRequestModelPicker={
        ccControls ? onRequestModelPicker : undefined
      }
      onRequestEffortPicker={
        ccControls ? onRequestEffortPicker : undefined
      }
      memoryEnabled={active?.memoryEnabled ?? null}
      profileEnabled={active?.profileEnabled ?? null}
    />
  );
}
