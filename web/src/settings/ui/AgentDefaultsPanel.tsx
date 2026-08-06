/** 展示只影响之后新建 Agent 会话的默认条件。 */

import { PopperSelect } from "../../components/ui/PopperSelect";
import { RUNTIME_OPTIONS } from "../../components/cc/newSessionOptions";
import { eligibleAgentSessionConfigurations } from "../application/selectors";
import type {
  AgentDefaults,
  ConfigurationCatalog,
} from "../domain/types";
import { PanelHeader } from "./SettingsPrimitives";
import { SettingsSwitch } from "./SettingsSwitch";

interface AgentDefaultsPanelProps {
  readonly catalog: ConfigurationCatalog | null;
  readonly draft: AgentDefaults;
  readonly dirty: boolean;
  readonly saving: boolean;
  readonly error: string | null;
  readonly conflict: boolean;
  readonly onChange: (patch: Partial<AgentDefaults>) => void;
  readonly onRetry: () => void;
  readonly onReload: () => void;
}

const FOLLOW_RUNTIME = "__follow_runtime__";
const NO_CONFIGURATION = "__no_configuration__";

/** 根据所选配置 runtime 展示对应权限，不把 Claude 和 Codex 权限混为一组。 */
export function AgentDefaultsPanel({
  catalog,
  draft,
  dirty,
  saving,
  error,
  conflict,
  onChange,
  onRetry,
  onReload,
}: AgentDefaultsPanelProps) {
  const availableConfigurations = catalog
    ? eligibleAgentSessionConfigurations(catalog)
    : [];
  const selected = availableConfigurations.find(
    (item) => item.id === draft.session_configuration_id,
  );
  const runtime = RUNTIME_OPTIONS.find((item) => item.value === selected?.runtime);
  const fallbackRuntime = availableConfigurations.find(
    (configuration) => configuration.runtime !== "direct_api",
  )?.runtime;
  const selectedRuntime = (selected?.runtime ?? fallbackRuntime) === "codex"
    ? "codex"
    : "claude_code";
  const permissionOptions = [
    { value: FOLLOW_RUNTIME, label: "跟随 Runtime" },
    ...(runtime?.permissions ?? []).map((item) => ({ value: item.value, label: item.label })),
  ];

  return (
    <section className="settings-panel" aria-labelledby="settings-agent-title">
      <PanelHeader
        id="settings-agent-title"
        title="Agent 默认条件"
        description="用于预填新建会话表单。历史会话恢复时仍沿用它自己的原生事实。"
        aside={saving ? "正在保存…" : dirty ? "等待自动保存" : "已自动保存"}
      />
      <section className="settings-section">
        <div className="settings-list">
          <div className="settings-row settings-setting-row">
            <div className="settings-row__body">
              <strong>默认 runtime</strong>
              <span>新建 Agent 会话的初始选择。</span>
            </div>
            <div className="settings-segment" role="group" aria-label="默认 runtime">
              {(["claude_code", "codex"] as const).map((runtimeKind) => {
                const runtimeConfigurations = availableConfigurations.filter(
                  (configuration) => configuration.runtime === runtimeKind,
                );
                return (
                  <button
                    type="button"
                    key={runtimeKind}
                    aria-pressed={selectedRuntime === runtimeKind}
                    disabled={!runtimeConfigurations.length}
                    onClick={() => onChange({
                      session_configuration_id: runtimeConfigurations[0]?.id ?? null,
                      permission: null,
                    })}
                  >
                    {runtimeKind === "claude_code" ? "Claude Code" : "Codex"}
                  </button>
                );
              })}
            </div>
          </div>
          <div className="settings-row settings-setting-row">
            <div className="settings-row__body">
              <strong>默认模型连接</strong>
              <span>按默认 runtime 过滤当前可用的完整会话配置。</span>
            </div>
          <PopperSelect
            ariaLabel="Agent 默认会话配置"
            value={draft.session_configuration_id ?? NO_CONFIGURATION}
            options={[
              { value: NO_CONFIGURATION, label: "不指定" },
              ...availableConfigurations.filter((configuration) => configuration.runtime === selectedRuntime).map((configuration) => ({
                value: configuration.id,
                label: `${configuration.name} · ${configuration.model}`,
              })),
            ]}
            onValueChange={(value) =>
              onChange({
                session_configuration_id: value === NO_CONFIGURATION ? null : value,
                permission: null,
              })
            }
            triggerClassName="settings-select is-wide"
          />
          </div>
          <DefaultToggle label="Memory" detail="向新会话提供长期记忆检索能力。" checked={draft.memory_enabled} onChange={(value) => onChange({ memory_enabled: value })} />
          <DefaultToggle label="Profile" detail="注入已确认的用户画像。" checked={draft.profile_enabled} onChange={(value) => onChange({ profile_enabled: value })} />
          <DefaultToggle label="Self" detail="注入持续主体说明。" checked={draft.self_enabled} onChange={(value) => onChange({ self_enabled: value })} />
          <div className="settings-row settings-setting-row">
            <div className="settings-row__body">
              <strong>权限</strong>
              <span>研讨参与者仍由其工作流固定权限，不读取这里的默认值。</span>
            </div>
          <PopperSelect
            ariaLabel="Agent 默认权限"
            value={draft.permission ?? FOLLOW_RUNTIME}
            options={permissionOptions}
            onValueChange={(value) => onChange({ permission: value === FOLLOW_RUNTIME ? null : value })}
            triggerClassName="settings-select is-wide"
            disabled={!selected}
          />
          </div>
        </div>
      </section>
      {error && (
        <div className="settings-error-box" role="alert">
          <span>{error}</span>
          <span className="settings-error-actions">
            <button type="button" onClick={onRetry}>重试</button>
            {conflict && <button type="button" onClick={onReload}>重载远端版本</button>}
          </span>
        </div>
      )}
    </section>
  );
}

interface DefaultToggleProps {
  readonly label: string;
  readonly detail: string;
  readonly checked: boolean;
  readonly onChange: (checked: boolean) => void;
}

/** 渲染带明确文本状态的布尔默认项。 */
function DefaultToggle({ label, detail, checked, onChange }: DefaultToggleProps) {
  return (
    <div className="settings-row settings-setting-row">
      <span className="settings-row__body">
        <strong>{label}</strong>
        <span>{detail}</span>
      </span>
      <span className="settings-setting-row__control">
        <span className="settings-control-note">默认{checked ? "开启" : "关闭"}</span>
        <SettingsSwitch label={`默认开启 ${label}`} checked={checked} onCheckedChange={onChange} />
      </span>
    </div>
  );
}
