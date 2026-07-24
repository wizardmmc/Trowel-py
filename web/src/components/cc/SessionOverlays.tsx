import type { ModelOption } from "../../api/cc";
import type {
  AgentModel,
} from "../../api/agent";
import type { Turn } from "../../stores/ccStore";
import { EffortPicker } from "./EffortPicker";
import { ModelPicker } from "./ModelPicker";
import {
  NewSessionDialog,
  type NewSessionConfig,
  type RuntimesState,
} from "./NewSessionDialog";
import { RevertConfirmModal } from "./RevertConfirmModal";

interface SessionOverlaysProps {
  readonly revert: {
    readonly lostTurns: readonly Turn[];
    readonly onConfirm: () => void;
    readonly onCancel: () => void;
  } | null;
  readonly newSession: {
    readonly workdir: string;
    readonly runtimesState: RuntimesState;
    readonly onRetryRuntimes: () => void;
    readonly creating: boolean;
    readonly error: string | null;
    readonly ccModels: readonly ModelOption[];
    readonly codexModels: readonly AgentModel[];
    readonly codexCatalogError: string | null;
    readonly onRetryCodexCatalog: () => void;
    readonly onCreate: (config: NewSessionConfig) => void;
    readonly onCancel: () => void;
  } | null;
  readonly modelPicker: {
    readonly models: readonly ModelOption[];
    readonly currentModel: string;
    readonly onSelect: (value: string) => void;
    readonly onCancel: () => void;
  } | null;
  readonly effortPicker: {
    readonly currentEffort: string | null;
    readonly onSelect: (value: string) => void;
    readonly onCancel: () => void;
  } | null;
}

export function SessionOverlays({
  revert,
  newSession,
  modelPicker,
  effortPicker,
}: SessionOverlaysProps) {
  return (
    <>
      {revert && (
        <RevertConfirmModal
          lostTurns={revert.lostTurns}
          onConfirm={revert.onConfirm}
          onCancel={revert.onCancel}
        />
      )}
      {newSession && (
        <NewSessionDialog
          workdir={newSession.workdir}
          runtimesState={newSession.runtimesState}
          onRetryRuntimes={newSession.onRetryRuntimes}
          creating={newSession.creating}
          error={newSession.error}
          ccModels={newSession.ccModels}
          codexModels={newSession.codexModels}
          codexCatalogError={newSession.codexCatalogError}
          onRetryCodexCatalog={newSession.onRetryCodexCatalog}
          onCreate={newSession.onCreate}
          onCancel={newSession.onCancel}
        />
      )}
      {modelPicker && (
        <ModelPicker
          models={modelPicker.models}
          currentModel={modelPicker.currentModel}
          onSelect={modelPicker.onSelect}
          onCancel={modelPicker.onCancel}
        />
      )}
      {effortPicker && (
        <EffortPicker
          currentEffort={effortPicker.currentEffort}
          onSelect={effortPicker.onSelect}
          onCancel={effortPicker.onCancel}
        />
      )}
    </>
  );
}
