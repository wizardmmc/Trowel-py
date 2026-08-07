/** 展示并编辑由普通 Agent、MCP Agent、研讨和后台任务共用的运行配置。 */

import { useState } from "react";
import { PopperSelect } from "../../components/ui/PopperSelect";
import { CLAUDE_EFFORT_LEVELS } from "../../lib/runtimeEffort";
import type {
  ConfigurationCatalog,
  Connection,
  RuntimeKind,
  SessionConfigurationDraft,
  SessionConfigurationEditorState,
} from "../domain/types";
import { compactConfigurationLabel } from "../domain/configurationLabels";
import { SettingsSwitch } from "./SettingsSwitch";
import { EmptyState, PanelHeader, StatusPill } from "./SettingsPrimitives";

interface RuntimeConfigurationsPanelProps {
  readonly catalog: ConfigurationCatalog | null;
  readonly editor: SessionConfigurationEditorState | null;
  readonly onOpen: (configurationId: string) => void;
  readonly onCreate: () => void;
  readonly onClose: () => void;
  readonly onChange: (patch: Partial<SessionConfigurationDraft>) => void;
  readonly onSave: () => void;
  readonly onArchive: () => void;
}

const UNSELECTED = "__unselected__";
const RUNTIME_LABELS = {
  claude_code: "Claude Code",
  codex: "Codex",
  direct_api: "Direct API",
} as const;
type RuntimeFilter = "all" | RuntimeKind;
const RUNTIME_FILTERS: readonly {
  readonly value: RuntimeFilter;
  readonly label: string;
}[] = [
  { value: "all", label: "全部" },
  { value: "claude_code", label: "Claude Code" },
  { value: "codex", label: "Codex" },
  { value: "direct_api", label: "Direct API" },
];

/** 在列表和编辑器之间切换，管理页始终保留完整连接来源。 */
export function RuntimeConfigurationsPanel({
  catalog,
  editor,
  onOpen,
  onCreate,
  onClose,
  onChange,
  onSave,
  onArchive,
}: RuntimeConfigurationsPanelProps) {
  const [runtimeFilter, setRuntimeFilter] = useState<RuntimeFilter>("all");
  if (editor) {
    return (
      <RuntimeConfigurationEditor
        catalog={catalog}
        editor={editor}
        onClose={onClose}
        onChange={onChange}
        onSave={onSave}
        onArchive={onArchive}
      />
    );
  }
  const configurations = catalog?.session_configurations ?? [];
  const connections = catalog?.connections ?? [];
  const filteredConfigurations = configurations.filter(
    (configuration) => runtimeFilter === "all" || configuration.runtime === runtimeFilter,
  );
  const visibleFilters = RUNTIME_FILTERS.filter(
    (filter) => filter.value !== "direct_api"
      || configurations.some((item) => item.runtime === "direct_api"),
  );
  return (
    <section className="settings-panel" aria-labelledby="settings-configurations-title">
      <PanelHeader
        id="settings-configurations-title"
        title="运行配置"
        description="把 Runtime、模型连接、模型和思考强度保存成一份可复用配置。已运行会话继续使用创建时冻结的事实。"
        aside={`${configurations.length} 项配置`}
      />
      <section className="settings-section settings-runtime-configurations">
        <div className="settings-section__head">
          <div>
            <h3>Agent 配置</h3>
            <p>修改只影响之后创建的会话和后台任务，既有会话继续使用冻结快照。</p>
          </div>
          <button type="button" className="settings-button is-primary" onClick={onCreate}>
            ＋ 添加配置
          </button>
        </div>
        {configurations.length === 0 ? (
          <EmptyState title="还没有运行配置" detail="新增后可用于 Agent 默认、MCP Agent、研讨和后台任务。" />
        ) : (
          <>
            <div className="settings-runtime-toolbar">
              <div className="settings-segment" role="group" aria-label="按 Runtime 筛选">
                {visibleFilters.map((filter) => {
                  const count = filter.value === "all"
                    ? configurations.length
                    : configurations.filter((item) => item.runtime === filter.value).length;
                  return (
                    <button
                      type="button"
                      key={filter.value}
                      aria-pressed={runtimeFilter === filter.value}
                      onClick={() => setRuntimeFilter(filter.value)}
                    >
                      {filter.label} · {count}
                    </button>
                  );
                })}
              </div>
              <span>{filteredConfigurations.length} 项配置</span>
            </div>
            <div className="settings-runtime-table">
              <div className="settings-runtime-table__head" aria-hidden="true">
                <span>模型连接 + 配置名称</span>
                <span>模型与强度</span>
                <span>选择时显示</span>
                <span>MCP Agent</span>
                <span />
              </div>
              {filteredConfigurations.map((configuration) => {
                const connection = connections.find(
                  (item) => item.id === configuration.connection_id,
                );
                const connectionName = connection?.name
                  ?? configuration.connection_name
                  ?? "连接已删除";
                return (
                  <button
                    type="button"
                    className="settings-runtime-table__row"
                    key={configuration.id}
                    onClick={() => onOpen(configuration.id)}
                  >
                    <span className="settings-runtime-name">
                      <span className={`settings-runtime-mark is-${configuration.runtime}`}>
                        {configuration.runtime === "claude_code"
                          ? "CC"
                          : configuration.runtime === "codex" ? "CX" : "API"}
                      </span>
                      <span className="settings-runtime-cell">
                        <strong>{connectionName} · {configuration.name}</strong>
                        <small>
                          {RUNTIME_LABELS[configuration.runtime]}
                          {configuration.runtime === "direct_api" ? " · 仅后台" : ""}
                        </small>
                      </span>
                    </span>
                    <span className="settings-runtime-cell">
                      <strong>{configuration.model}</strong>
                      <small>{configuration.effort ?? "跟随 Runtime 默认"}</small>
                    </span>
                    <span className="settings-runtime-cell">
                      {configuration.stable_alias ? (
                        <code className="settings-runtime-alias">{configuration.stable_alias}</code>
                      ) : (
                        <strong>{compactConfigurationLabel(configuration, connections)}</strong>
                      )}
                      <small>{configuration.stable_alias ? "稳定调用别名" : "模型连接 · 配置名称"}</small>
                    </span>
                    <span className="settings-runtime-cell">
                      {configuration.agent_callable
                        ? <StatusPill status="available" label="允许调用" />
                        : <span className="settings-runtime-muted">未开放</span>}
                      {configuration.availability !== "available"
                        && <StatusPill status="unavailable" label="不可用" />}
                    </span>
                    <span className="settings-runtime-chevron" aria-hidden="true">›</span>
                  </button>
                );
              })}
            </div>
            <p className="settings-runtime-note">
              紧凑选择器有别名时只显示别名；没有别名时显示“模型连接 · 配置名称”。
            </p>
          </>
        )}
      </section>
    </section>
  );
}

interface RuntimeConfigurationEditorProps {
  readonly catalog: ConfigurationCatalog | null;
  readonly editor: SessionConfigurationEditorState;
  readonly onClose: () => void;
  readonly onChange: (patch: Partial<SessionConfigurationDraft>) => void;
  readonly onSave: () => void;
  readonly onArchive: () => void;
}

/** 编辑启动组合和独立的 Agent 调用策略，不复制连接凭据或会话上下文。 */
function RuntimeConfigurationEditor({
  catalog,
  editor,
  onClose,
  onChange,
  onSave,
  onArchive,
}: RuntimeConfigurationEditorProps) {
  const connections = catalog?.connections ?? [];
  const connection = connections.find((item) => item.id === editor.draft.connection_id);
  const selectedRuntime = connection?.runtime
    ?? connections.find((item) => item.runtime !== "direct_api")?.runtime
    ?? connections[0]?.runtime
    ?? "claude_code";
  const runtimeConnections = connections.filter(
    (item) => item.runtime === selectedRuntime,
  );
  const models = connection ? configurationModels(connection) : [];
  const efforts = connection ? configurationEfforts(connection, editor.draft.model) : [];
  const canSave = Boolean(
    editor.draft.name.trim()
    && connection
    && editor.draft.model,
  );
  return (
    <section className="settings-panel" aria-labelledby="settings-configuration-editor-title">
      <header className="settings-editor-header">
        <button type="button" className="settings-back" onClick={onClose}>← 返回</button>
        <div>
          <span>{editor.configurationId ? "编辑配置" : "新增配置"}</span>
          <h2 id="settings-configuration-editor-title">
            {editor.draft.name.trim() || "未命名配置"}
          </h2>
        </div>
        <StatusPill
          status={editor.error ? "unavailable" : "available"}
          label={editor.error ? "需要处理" : editor.configurationId ? "配置可用" : "尚未保存"}
        />
      </header>
      <section className="settings-form-block">
        <h3>基本信息</h3>
        <p>配置名称解释用途；稳定别名供 Agent MCP 调用，并在紧凑选择器中优先显示。</p>
        <div className="settings-form-grid">
          <label className="settings-field">
            <span>配置名称</span>
            <input value={editor.draft.name} onChange={(event) => onChange({ name: event.target.value })} placeholder="例如：Sol High" />
          </label>
          <label className="settings-field">
            <span>稳定调用别名（可选）</span>
            <span className="settings-alias-input">
              <span>mcp agent</span>
              <input
                value={editor.draft.stable_alias ?? ""}
                onChange={(event) => {
                  const stableAlias = event.target.value || null;
                  onChange({
                    stable_alias: stableAlias,
                    agent_callable: stableAlias ? editor.draft.agent_callable : false,
                  });
                }}
                placeholder="例如：codex1"
              />
            </span>
            <small>可以改回本配置使用过的旧别名；其他配置使用过的别名仍不会被抢占。</small>
          </label>
        </div>
      </section>
      <section className="settings-form-block">
        <h3>启动组合</h3>
        <p>凭据仍由模型连接管理。修改这里不改写已经创建的会话。</p>
        <div className="settings-form-grid">
          <label className="settings-field">
            <span>Runtime</span>
            <PopperSelect
              ariaLabel="运行配置 Runtime"
              value={selectedRuntime}
              options={RUNTIME_FILTERS.filter((item) => item.value !== "all").map(
                (item) => ({
                  value: item.value,
                  label: item.label,
                  disabled: !connections.some((connectionItem) => connectionItem.runtime === item.value),
                }),
              )}
              onValueChange={(runtime) => {
                const nextConnection = connections.find((item) => item.runtime === runtime);
                onChange({
                  connection_id: nextConnection?.id ?? "",
                  model: "",
                  effort: null,
                  agent_callable: runtime === "direct_api" ? false : editor.draft.agent_callable,
                });
              }}
              triggerClassName="settings-select is-wide"
            />
            <small>决定连接类型和原生会话实现。</small>
          </label>
          <label className="settings-field">
            <span>模型连接</span>
            <PopperSelect
              ariaLabel="运行配置模型连接"
              value={editor.draft.connection_id || UNSELECTED}
              options={[
                { value: UNSELECTED, label: "选择模型连接", disabled: true },
                ...runtimeConnections.map((item) => ({ value: item.id, label: item.name })),
              ]}
              onValueChange={(connectionId) => onChange({
                connection_id: connectionId === UNSELECTED ? "" : connectionId,
                model: "",
                effort: null,
                agent_callable: connections.find((item) => item.id === connectionId)?.runtime === "direct_api"
                  ? false
                  : editor.draft.agent_callable,
              })}
              triggerClassName="settings-select is-wide"
            />
            <small>凭据仍由模型连接管理，不复制到运行配置。</small>
          </label>
          <label className="settings-field">
            <span>模型</span>
            <PopperSelect
              ariaLabel="运行配置模型"
              value={editor.draft.model || UNSELECTED}
              options={[
                { value: UNSELECTED, label: "选择模型", disabled: true },
                ...models.map((model) => ({ value: model, label: model })),
              ]}
              onValueChange={(model) => onChange({ model: model === UNSELECTED ? "" : model, effort: null })}
              triggerClassName="settings-select is-wide"
              disabled={!connection}
            />
            <small>候选来自所选连接保存的模型目录。</small>
          </label>
          <label className="settings-field">
            <span>思考强度</span>
            <PopperSelect
              ariaLabel="运行配置思考强度"
              value={editor.draft.effort ?? UNSELECTED}
              options={[
                { value: UNSELECTED, label: "跟随 Runtime 默认" },
                ...efforts.map((effort) => ({ value: effort, label: effort })),
              ]}
              onValueChange={(effort) => onChange({ effort: effort === UNSELECTED ? null : effort })}
              triggerClassName="settings-select is-wide"
              disabled={!editor.draft.model}
            />
            <small>实际值会作为会话启动快照的一部分冻结。</small>
          </label>
        </div>
        {connection && (
          <small>{RUNTIME_LABELS[connection.runtime]} · {connection.name}{connection.runtime === "direct_api" ? " · 仅后台任务" : ""}</small>
        )}
      </section>
      <section className="settings-form-block">
        <h3>MCP Agent</h3>
        <p>只有明确开放且填写稳定别名的配置，才会进入之后新建父会话的冻结调用清单。</p>
        <div className="settings-runtime-callable">
          <span className="settings-row__body">
            <strong>允许 Agent 调用</strong>
            <span>{connection?.runtime === "direct_api" ? "Direct API 不能作为 MCP Agent。" : "父 Agent 只提交别名和任务，不能临时改模型与强度。"}</span>
          </span>
          <SettingsSwitch
            label="允许 Agent 调用"
            checked={editor.draft.agent_callable}
            disabled={
              connection?.runtime === "direct_api"
              || !editor.draft.stable_alias?.trim()
            }
            onCheckedChange={(checked) => onChange({ agent_callable: checked })}
          />
        </div>
      </section>
      <section className="settings-form-block">
        <h3>调用预览</h3>
        <p>自然语言只选择稳定别名；模型连接、model 和 effort 不由父 Agent 临时拼接。</p>
        <div className="settings-runtime-preview">
          <pre>{runtimeConfigurationPreview(editor.draft, connection)}</pre>
          <div>
            <strong>创建时冻结</strong>
            <p>编辑配置只影响之后创建的普通会话、MCP 子 Agent 和后台任务。</p>
          </div>
        </div>
      </section>
      {editor.error && <div className="settings-error-box" role="alert"><span>{editor.error}</span></div>}
      <footer className="settings-panel__footer settings-editor-footer">
        <div>{editor.configurationId && <button type="button" className="settings-button is-danger" disabled={editor.archiving} onClick={onArchive}>{editor.archiving ? "归档中…" : "归档配置"}</button>}</div>
        <div>
          <span>{editor.dirty ? "有尚未保存的修改" : "已与持久配置一致"}</span>
          <button type="button" className="settings-button is-primary" disabled={!canSave || editor.saving || (!editor.dirty && Boolean(editor.configurationId))} onClick={onSave}>{editor.saving ? "保存中…" : "保存配置"}</button>
        </div>
      </footer>
    </section>
  );
}

/** 返回连接能够写入运行配置的模型 ID；Claude 使用角色别名。 */
function configurationModels(connection: Connection): readonly string[] {
  if (connection.runtime === "claude_code") {
    return ["opus", "sonnet", "fable", "haiku"].filter(
      (role) => Boolean(connection.claude_role_models[role]),
    );
  }
  if (connection.runtime === "codex") {
    return connection.codex_catalog.map((item) => item.id);
  }
  return connection.catalog.models;
}

/** 返回 Runtime 声明的 effort 候选；Claude 使用 CLI 公开集合。 */
function configurationEfforts(
  connection: Connection,
  model: string,
): readonly string[] {
  if (connection.runtime === "claude_code") return CLAUDE_EFFORT_LEVELS;
  if (connection.runtime !== "codex") return [];
  return connection.codex_catalog.find((item) => item.id === model)?.supported_efforts ?? [];
}

/** 生成不包含凭据和工作目录的稳定调用预览。 */
function runtimeConfigurationPreview(
  draft: SessionConfigurationDraft,
  connection: Connection | undefined,
): string {
  const runtime = connection ? RUNTIME_LABELS[connection.runtime] : "尚未选择 Runtime";
  const connectionName = connection?.name ?? "尚未选择连接";
  const model = draft.model || "尚未选择模型";
  const effort = draft.effort ?? "跟随 Runtime 默认";
  const alias = draft.stable_alias?.trim();
  if (draft.agent_callable && alias) {
    return `用户：启动 mcp ${alias}，执行任务\n\nTrowel：解析 ${alias}\n${runtime} · ${connectionName} · ${model} · ${effort}`;
  }
  const display = alias || `${connectionName} · ${draft.name.trim() || "未命名配置"}`;
  return `选择时显示：${display}\n\nMCP Agent：未允许调用\n${runtime} · ${model} · ${effort}`;
}
