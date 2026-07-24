import type { Runtime } from "../../api/agent";

interface SettingOption {
  readonly value: string;
  readonly label: string;
}

interface RuntimeSettingsProps {
  readonly runtime: Runtime;
  readonly creating: boolean;
  readonly models: readonly SettingOption[];
  readonly selectedModel: string;
  readonly efforts: readonly SettingOption[];
  readonly selectedEffort: string;
  readonly permissions: readonly SettingOption[];
  readonly selectedPermission: string;
  readonly codexCatalogError: string | null;
  readonly confirmFullAccess: boolean;
  readonly onSelectModel: (model: string) => void;
  readonly onSelectEffort: (effort: string) => void;
  readonly onSelectPermission: (permission: string) => void;
  readonly onConfirmFullAccess: () => void;
  readonly onRetryCodexCatalog?: () => void;
}

export function RuntimeSettings({
  runtime,
  creating,
  models,
  selectedModel,
  efforts,
  selectedEffort,
  permissions,
  selectedPermission,
  codexCatalogError,
  confirmFullAccess,
  onSelectModel,
  onSelectEffort,
  onSelectPermission,
  onConfirmFullAccess,
  onRetryCodexCatalog,
}: RuntimeSettingsProps) {
  return (
    <>
      <div className="cc-dialog__section-label">Model</div>
      {runtime === "codex" && codexCatalogError !== null && (
        <div className="cc-dialog__diag" role="alert">
          Codex model catalog 不可用：{codexCatalogError}
          {onRetryCodexCatalog && (
            <button
              type="button"
              className="cc-dialog__btn"
              onClick={onRetryCodexCatalog}
              disabled={creating}
              style={{ marginLeft: 8 }}
            >
              重试
            </button>
          )}
        </div>
      )}
      <OptionRow
        options={models}
        selected={selectedModel}
        creating={creating}
        onSelect={onSelectModel}
      />

      <div className="cc-dialog__section-label">Effort</div>
      <OptionRow
        options={efforts}
        selected={selectedEffort}
        creating={creating}
        onSelect={onSelectEffort}
      />

      <div className="cc-dialog__section-label">Permission</div>
      <OptionRow
        options={permissions}
        selected={selectedPermission}
        creating={creating}
        dangerValue="danger-full-access"
        onSelect={onSelectPermission}
      />

      {confirmFullAccess && runtime === "codex" && (
        <div className="cc-dialog__danger-confirm" role="alert">
          <span>
            Full access 会关闭 sandbox，并使用 never approval；Codex
            可读写工作区外文件并联网。
          </span>
          <button
            type="button"
            className="cc-dialog__btn cc-dialog__btn--danger"
            onClick={onConfirmFullAccess}
          >
            确认 Full access
          </button>
        </div>
      )}
      {runtime === "codex" && selectedPermission === "workspace-write" && (
        <div className="cc-dialog__diag" role="status">
          Workspace 使用 on-request；遇到原生审批请求时，本轮会暂停并等待确认。
          需要实际写入时可改用 Full access。
        </div>
      )}
    </>
  );
}

function OptionRow({
  options,
  selected,
  creating,
  dangerValue,
  onSelect,
}: {
  readonly options: readonly SettingOption[];
  readonly selected: string;
  readonly creating: boolean;
  readonly dangerValue?: string;
  readonly onSelect: (value: string) => void;
}) {
  return (
    <div className="cc-dialog__option-row">
      {options.map((option) => (
        <button
          key={option.label}
          type="button"
          className={
            "cc-dialog__option" +
            (selected === option.value ? " cc-dialog__option--selected" : "") +
            (option.value === dangerValue
              ? " cc-dialog__option--danger"
              : "")
          }
          disabled={creating}
          onClick={() => onSelect(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}
