import type { ModelOption } from "../../api/cc";
import { MemoryProfileChip } from "./MemoryProfileChip";
import {
  ModelEffortChip,
  type EffortControlOption,
} from "./ModelEffortChip";
import { PermissionFactsChip } from "./PermissionFactsChip";

export interface PermissionFacts {
  readonly requested: string | null;
  readonly profile: string | null;
  readonly sandbox: string | null;
  readonly approval: string | null;
  readonly network: boolean | null;
  readonly label: string | null;
}

interface ComposerToolbarProps {
  readonly streaming: boolean;
  readonly sendDisabled: boolean;
  readonly onSend: () => void;
  readonly onInterrupt: () => void;
  readonly models?: readonly ModelOption[];
  readonly efforts?: readonly EffortControlOption[];
  readonly currentModelAlias?: string | null;
  readonly currentEffort?: string | null;
  readonly onPickModel?: (alias: string) => void;
  readonly onPickEffort?: (value: string) => void;
  readonly modelCatalogError?: string | null;
  readonly onRetryModelCatalog?: () => void;
  readonly settingsDisabled: boolean;
  readonly permissionFacts?: PermissionFacts | null;
  readonly memoryEnabled?: boolean | null;
  readonly profileEnabled?: boolean | null;
}

export function ComposerToolbar({
  streaming,
  sendDisabled,
  onSend,
  onInterrupt,
  models,
  efforts,
  currentModelAlias,
  currentEffort,
  onPickModel,
  onPickEffort,
  modelCatalogError,
  onRetryModelCatalog,
  settingsDisabled,
  permissionFacts,
  memoryEnabled,
  profileEnabled,
}: ComposerToolbarProps) {
  return (
    <div className="cc-composer__bar">
      {models && onPickModel && onPickEffort && (
        <ModelEffortChip
          models={models}
          efforts={efforts}
          currentModelAlias={currentModelAlias ?? null}
          currentEffort={currentEffort ?? null}
          onPickModel={onPickModel}
          onPickEffort={onPickEffort}
          catalogError={modelCatalogError}
          onRetryCatalog={onRetryModelCatalog}
          disabled={settingsDisabled}
        />
      )}
      {permissionFacts && <PermissionFactsChip {...permissionFacts} />}
      {memoryEnabled != null && profileEnabled != null && (
        <MemoryProfileChip
          memoryEnabled={memoryEnabled}
          profileEnabled={profileEnabled}
        />
      )}
      <span className="cc-composer__spacer" />
      <button
        type="button"
        className={`cc-composer__send${streaming ? " cc-composer__send--stop" : ""}`}
        onClick={streaming ? onInterrupt : onSend}
        disabled={!streaming && sendDisabled}
        aria-label={streaming ? "中断" : "发送"}
        title={streaming ? "中断（Esc）" : "发送（Enter）"}
      >
        {streaming ? (
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <rect x="6" y="6" width="12" height="12" rx="2" />
          </svg>
        ) : (
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M12 19V5M5 12l7-7 7 7" />
          </svg>
        )}
      </button>
    </div>
  );
}
