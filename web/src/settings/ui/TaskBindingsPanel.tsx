/** 展示五项后台任务各自独立的配置绑定。 */

import { PopperSelect } from "../../components/ui/PopperSelect";
import { eligibleSessionConfigurations } from "../application/selectors";
import { compactConfigurationLabel } from "../domain/configurationLabels";
import type {
  ConfigurationCatalog,
  TaskId,
} from "../domain/types";
import { TASKS } from "../domain/types";
import { SettingsSwitch } from "./SettingsSwitch";
import { PanelHeader, StatusPill } from "./SettingsPrimitives";

interface TaskBindingsPanelProps {
  readonly catalog: ConfigurationCatalog | null;
  readonly drafts: Readonly<Record<TaskId, string | null>>;
  readonly enabled: Readonly<Record<TaskId, boolean>>;
  readonly saving: Readonly<Record<TaskId, boolean>>;
  readonly errors: Readonly<Record<TaskId, string | null>>;
  readonly onChange: (taskId: TaskId, configurationId: string | null) => void;
  readonly onToggle: (taskId: TaskId, enabled: boolean) => void;
  readonly onRetry: (taskId: TaskId) => void;
  readonly onReload: (taskId: TaskId) => void;
}

const UNBOUND = "__unbound__";

/** 每行只展示后端 capability 明确允许当前任务的 available 配置。 */
export function TaskBindingsPanel({
  catalog,
  drafts,
  enabled,
  saving,
  errors,
  onChange,
  onToggle,
  onRetry,
  onReload,
}: TaskBindingsPanelProps) {
  return (
    <section className="settings-panel" aria-labelledby="settings-tasks-title">
      <PanelHeader
        id="settings-tasks-title"
        title="后台任务"
        description="为提炼与整理任务选择运行配置。每项任务独立启停，不会自动回退到其他配置。"
        aside="额度轮询未启用"
      />
      {TASK_GROUPS.map((group) => (
        <section className="settings-section" key={group.title}>
          <div className="settings-section__head">
            <div>
              <h3>{group.title}</h3>
              <p>{group.description}</p>
            </div>
          </div>
          <div className="settings-list">
            {TASKS.filter((task) => group.tasks.includes(task.id)).map((task) => (
              <TaskBindingRow
                key={task.id}
                task={task}
                catalog={catalog}
                draft={drafts[task.id]}
                enabled={enabled[task.id]}
                saving={saving[task.id]}
                error={errors[task.id]}
                onChange={(configurationId) => onChange(task.id, configurationId)}
                onToggle={(checked) => onToggle(task.id, checked)}
                onRetry={() => onRetry(task.id)}
                onReload={() => onReload(task.id)}
              />
            ))}
          </div>
        </section>
      ))}
    </section>
  );
}

interface TaskBindingRowProps {
  readonly task: (typeof TASKS)[number];
  readonly catalog: ConfigurationCatalog | null;
  readonly draft: string | null;
  readonly enabled: boolean;
  readonly saving: boolean;
  readonly error: string | null;
  readonly onChange: (configurationId: string | null) => void;
  readonly onToggle: (enabled: boolean) => void;
  readonly onRetry: () => void;
  readonly onReload: () => void;
}

/** 收口单项资格、失效提示和自动保存恢复交互。 */
function TaskBindingRow({
  task,
  catalog,
  draft,
  enabled,
  saving,
  error,
  onChange,
  onToggle,
  onRetry,
  onReload,
}: TaskBindingRowProps) {
  const options = catalog
    ? eligibleSessionConfigurations(catalog, task.id)
    : [];
  const binding = catalog?.task_bindings.find((item) => item.task_id === task.id);
  const persistedId = binding?.session_configuration_id ?? null;
  const persisted = catalog?.session_configurations.find(
    (item) => item.id === persistedId,
  );
  const staleBinding = persistedId !== null &&
    !options.some((item) => item.id === persistedId);
  const staleReason = !persisted
    ? "配置已删除"
    : persisted.availability !== "available"
      ? persisted.disabled_reason ?? "连接身份或模型目录已经变化"
      : "当前 capability 不再允许这项任务";
  const staleDraftOption = draft && !options.some((item) => item.id === draft)
    ? [{ value: draft, label: "原配置已失效", disabled: true }]
    : [];

  return (
    <div className="settings-row settings-task-row">
      <div className="settings-row__body">
        <div className="settings-row__title-line">
          <strong>{task.label}</strong>
          {staleBinding && (
            <StatusPill status="unavailable" label="原绑定已失效" />
          )}
        </div>
        <span>{task.description}</span>
        {staleBinding && <small>原配置不再可用：{staleReason}</small>}
        {error && <small className="settings-error">{error}</small>}
        {saving && <small>正在保存…</small>}
      </div>
      <div className="settings-task-row__controls">
        <PopperSelect
          ariaLabel={`${task.label}会话配置`}
          value={draft ?? UNBOUND}
          options={[
            { value: UNBOUND, label: "先选择配置", disabled: true },
            ...staleDraftOption,
            ...options.map((configuration) => ({
              value: configuration.id,
              label: compactConfigurationLabel(configuration, catalog?.connections ?? []),
            })),
          ]}
          onValueChange={(value) => onChange(value === UNBOUND ? null : value)}
          triggerClassName="settings-select"
        />
        <SettingsSwitch
          label={`启用${task.label}`}
          checked={enabled}
          disabled={draft === null}
          onCheckedChange={onToggle}
        />
        {error && (
          <span className="settings-error-actions">
            <button type="button" className="settings-button" onClick={onRetry}>重试</button>
            <button type="button" className="settings-button is-quiet" onClick={onReload}>重载</button>
          </span>
        )}
      </div>
    </div>
  );
}

const TASK_GROUPS: readonly {
  readonly title: string;
  readonly description: string;
  readonly tasks: readonly TaskId[];
}[] = [
  {
    title: "Memory 与 Profile",
    description: "会话结束后生成可检索记忆与待确认画像建议。",
    tasks: ["memory_refine", "profile_distill"],
  },
  {
    title: "整理",
    description: "Daily、周整理和月整理各自使用既有来源与能力门禁。",
    tasks: ["memory_daily", "memory_weekly", "memory_monthly"],
  },
];
