/** 展示连接列表及按 runtime 分开的非敏感编辑表单。 */

import type { FormEvent } from "react";
import { PopperSelect } from "../../components/ui/PopperSelect";
import type {
  CodexCatalogEntry,
  ConfigurationCatalog,
  Connection,
  ConnectionDraft,
  ConnectionEditorState,
  ConnectionKind,
  SecretKind,
} from "../domain/types";
import { CLAUDE_ROLES } from "../domain/types";
import { buildConnectionPreview } from "./connectionPreview";
import { EmptyState, PanelHeader, StatusPill } from "./SettingsPrimitives";

interface ConnectionsPanelProps {
  readonly catalog: ConfigurationCatalog | null;
  readonly editor: ConnectionEditorState | null;
  readonly runtimeFilter: "claude_code" | "codex";
  readonly onRuntimeFilterChange: (runtime: "claude_code" | "codex") => void;
  readonly onOpen: (connectionId: string) => void;
  readonly onCreate: (kind: ConnectionKind) => void;
  readonly onNewKindChange: (kind: ConnectionKind) => void;
  readonly onClose: () => void;
  readonly onDraftChange: (patch: Partial<ConnectionDraft>) => void;
  readonly onRoleChange: (role: string, model: string) => void;
  readonly onSave: () => void;
  readonly onDelete: () => void;
  readonly onFetchModels: () => void;
  readonly onWriteSecret: (kind: SecretKind, value: string) => Promise<void>;
  readonly onDeleteSecret: (kind: SecretKind) => void;
  readonly onStartOfficialLogin: () => void;
  readonly onOpenOfficialLogin: (url: string) => void;
  readonly onRefreshOfficialAccount: () => void;
  readonly onReload: () => void;
}

const UNSET_MODEL = "__unset_model__";
const EFFORTS = ["low", "medium", "high", "xhigh"] as const;

/** 在列表和全宽编辑器之间切换，避免把 runtime 差异塞进同一套字段。 */
export function ConnectionsPanel(props: ConnectionsPanelProps) {
  return props.editor ? <ConnectionEditor {...props} editor={props.editor} /> : <ConnectionList {...props} />;
}

/** 按 Agent 连接与 direct API 分组展示已保存事实。 */
function ConnectionList({ catalog, runtimeFilter, onRuntimeFilterChange, onOpen, onCreate }: ConnectionsPanelProps) {
  const agentConnections = catalog?.connections.filter((item) => item.runtime !== "direct_api") ?? [];
  const directConnections = catalog?.connections.filter((item) => item.runtime === "direct_api") ?? [];
  const filteredConnections = agentConnections.filter((item) => item.runtime === runtimeFilter);
  const runtimeCount = (runtime: "claude_code" | "codex") => agentConnections.filter((item) => item.runtime === runtime).length;
  return (
    <section className="settings-panel" aria-labelledby="settings-connections-title">
      <PanelHeader
        id="settings-connections-title"
        title="模型连接"
        description="一项连接包含服务入口、认证、协议和模型范围。创建会话时先选 Runtime，再选择完整连接。"
        aside={`${catalog?.connections.length ?? 0} 项连接`}
      />
      <section className="settings-section">
        <div className="settings-section__head">
          <div><h3>Agent 连接</h3><p>Claude Code 与 Codex 使用各自的配置表单；已运行会话继续使用冻结配置。</p></div>
          <button type="button" className="settings-button is-primary" onClick={() => onCreate(runtimeFilter === "codex" ? "codex_official" : "claude_compatible")}>
            <PlusIcon />添加连接
          </button>
        </div>
        <div className="settings-connection-toolbar">
          <div className="settings-segment" role="group" aria-label="按 Runtime 筛选连接">
            <button type="button" aria-pressed={runtimeFilter === "claude_code"} onClick={() => onRuntimeFilterChange("claude_code")}>Claude Code · {runtimeCount("claude_code")}</button>
            <button type="button" aria-pressed={runtimeFilter === "codex"} onClick={() => onRuntimeFilterChange("codex")}>Codex · {runtimeCount("codex")}</button>
          </div>
          <span>{filteredConnections.length} 项 {runtimeFilter === "codex" ? "Codex" : "Claude Code"} 连接</span>
        </div>
        <ConnectionTable connections={filteredConnections} onOpen={onOpen} />
        <p className="settings-save-note">模型与强度是每项连接的最近使用记忆，不属于连接 identity；创建失败不会改写。</p>
      </section>
      <section className="settings-section">
        <div className="settings-section__head">
          <div><h3>后台任务 Direct API</h3><p>只用于不需要 shell、MCP、审批和原生恢复的提炼任务。</p></div>
          <button type="button" className="settings-button" onClick={() => onCreate("direct_api")}>配置</button>
        </div>
        <ConnectionTable connections={directConnections} onOpen={onOpen} compact />
        <p className="settings-save-note">这里保存的内容不会覆盖 Claude 或 Codex 的原生配置。</p>
      </section>
    </section>
  );
}

interface ConnectionTableProps {
  readonly connections: NonNullable<ConfigurationCatalog>["connections"];
  readonly onOpen: (connectionId: string) => void;
  readonly compact?: boolean;
}

/** 按 mockup 表格列展示连接的脱敏事实。 */
function ConnectionTable({ connections, onOpen, compact = false }: ConnectionTableProps) {
  return (
    <div className="settings-connection-table">
      {!connections.length ? (
        <EmptyState title="暂无连接" detail="从上方选择一种连接类型开始配置。" />
      ) : (
        <div>
          {connections.map((connection) => (
            <button
              type="button"
              className={`settings-connection-row${compact ? " is-compact" : ""}`}
              key={connection.id}
              onClick={() => onOpen(connection.id)}
            >
              <span className="settings-connection-runtime">
                <span className={`settings-status-dot is-${connection.validation_status === "valid" ? "available" : "unknown"}`} />
                <span><strong>{connection.name}</strong><small>{runtimeLabel(connection.runtime)}</small></span>
              </span>
              {!compact && <span className="settings-connection-main"><strong>{lastChoiceLabel(connection)}</strong><small>{connectionDetail(connection)}</small></span>}
              <span className="settings-connection-provider"><strong>{connectionKindLabel(connection.kind)}</strong><code>{connectionProviderLabel(connection)}</code></span>
              <span className="settings-connection-actions">
                <StatusPill status={connection.validation_status === "valid" ? "available" : "unknown"} label={connection.validation_status === "valid" ? "已验证" : "待验证"} />
                <span className="settings-icon-button" aria-hidden="true"><SlidersIcon /></span>
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/** 渲染连接共用头部和 runtime 专属表单。 */
function ConnectionEditor({
  catalog,
  editor,
  onClose,
  onNewKindChange,
  onDraftChange,
  onRoleChange,
  onSave,
  onDelete,
  onFetchModels,
  onWriteSecret,
  onDeleteSecret,
  onStartOfficialLogin,
  onOpenOfficialLogin,
  onRefreshOfficialAccount,
  onReload,
}: ConnectionsPanelProps & { readonly editor: ConnectionEditorState }) {
  const saved = catalog?.connections.find((item) => item.id === editor.connectionId);
  const requiresBaseUrl = editor.draft.kind !== "codex_official";
  const canSave = Boolean(editor.draft.name.trim() && (!requiresBaseUrl || editor.draft.base_url?.trim()));
  return (
    <section className="settings-panel settings-connection-editor" aria-labelledby="settings-connection-editor-title">
      <header className="settings-editor-header">
        <button type="button" className="settings-back" onClick={onClose}>← 返回连接</button>
        <div>
          <span>{editor.connectionId ? "编辑连接" : "新增连接"}</span>
          <h2 id="settings-connection-editor-title">{editor.draft.name || connectionKindLabel(editor.draft.kind)}</h2>
        </div>
        <StatusPill
          status={saved?.auth.status === "configured" || saved?.auth.status === "referenced" ? "available" : "unknown"}
          label={saved ? authStatusLabel(saved.auth.status) : "先保存供应商"}
        />
      </header>

      {!editor.connectionId && (
        <>
          <section className="settings-form-block settings-runtime-choice-block">
            <h3>1. 选择 Runtime</h3>
            <p>Runtime 决定后续字段，也决定这项连接会出现在哪些会话里。</p>
            <div className="settings-runtime-choice" role="group" aria-label="连接 Runtime">
              <button type="button" aria-pressed={editor.draft.runtime === "claude_code"} onClick={() => onNewKindChange("claude_compatible")}>
                <strong>Claude Code</strong><small>Anthropic 兼容连接与角色模型映射</small>
              </button>
              <button type="button" aria-pressed={editor.draft.runtime === "codex"} onClick={() => onNewKindChange("codex_official")}>
                <strong>Codex</strong><small>Official 登录或第三方 Responses provider</small>
              </button>
            </div>
          </section>
          {editor.draft.runtime === "codex" && (
            <section className="settings-form-block settings-kind-choice-block">
              <h3>2. 连接种类</h3>
              <p>Official 沿用 Codex 原生登录；第三方连接使用 OpenAI Responses 兼容接口。</p>
              <div className="settings-runtime-choice" role="group" aria-label="Codex 连接种类">
                <button type="button" aria-pressed={editor.draft.kind === "codex_official"} onClick={() => onNewKindChange("codex_official")}>
                  <strong>OpenAI Official</strong><small>由 Trowel 管理独立的 ChatGPT 登录账号</small>
                </button>
                <button type="button" aria-pressed={editor.draft.kind === "codex_custom"} onClick={() => onNewKindChange("codex_custom")}>
                  <strong>第三方 Responses</strong><small>API key、请求地址与模型 catalog</small>
                </button>
              </div>
            </section>
          )}
        </>
      )}

      <div className="settings-form-section">
        <label className="settings-field">
          <span>供应商名称</span>
          <input
            value={editor.draft.name}
            maxLength={120}
            onChange={(event) => onDraftChange({ name: event.target.value })}
            placeholder="例如：GLM Claude"
          />
        </label>
        <div className="settings-field">
          <span>连接类型</span>
          <strong className="settings-readonly-value">{connectionKindLabel(editor.draft.kind)}</strong>
          <small>{editor.connectionId ? "已保存连接不切换类型，避免旧字段与模型身份混杂。" : "可在上方切换 Runtime 与连接种类。"}</small>
        </div>
      </div>

      {editor.draft.kind === "claude_compatible" && (
        <ClaudeConnectionFields
          editor={editor}
          savedAuthStatus={saved?.auth.status ?? "missing"}
          onDraftChange={onDraftChange}
          onRoleChange={onRoleChange}
          onFetchModels={onFetchModels}
          onWriteSecret={onWriteSecret}
          onDeleteSecret={onDeleteSecret}
        />
      )}
      {editor.draft.kind === "codex_official" && (
        <CodexOfficialFields
          editor={editor}
          savedProxyStatus={saved?.proxy.password_status ?? "missing"}
          onDraftChange={onDraftChange}
          onWriteSecret={onWriteSecret}
          onDeleteSecret={onDeleteSecret}
          onFetchModels={onFetchModels}
          onStartLogin={onStartOfficialLogin}
          onOpenLogin={onOpenOfficialLogin}
          onRefreshAccount={onRefreshOfficialAccount}
        />
      )}
      {editor.draft.kind === "codex_custom" && (
        <CodexCustomFields
          editor={editor}
          savedAuthStatus={saved?.auth.status ?? "missing"}
          onDraftChange={onDraftChange}
          onFetchModels={onFetchModels}
          onWriteSecret={onWriteSecret}
          onDeleteSecret={onDeleteSecret}
        />
      )}
      {editor.draft.kind === "direct_api" && (
        <DirectApiFields
          editor={editor}
          savedAuthStatus={saved?.auth.status ?? "missing"}
          onDraftChange={onDraftChange}
          onFetchModels={onFetchModels}
          onWriteSecret={onWriteSecret}
          onDeleteSecret={onDeleteSecret}
        />
      )}

      <Preview editor={editor} authStatus={saved?.auth.status ?? "missing"} />

      {editor.error && (
        <div className="settings-error-box" role="alert">
          <span>{editor.error}</span>
          {editor.conflict && <button type="button" onClick={onReload}>重载远端版本</button>}
        </div>
      )}
      <footer className="settings-panel__footer settings-editor-footer">
        <div>
          {editor.connectionId && (
            <button type="button" className="settings-button is-danger" disabled={editor.deleting} onClick={onDelete}>
              {editor.deleting ? "删除中…" : "删除连接"}
            </button>
          )}
        </div>
        <div>
          <span>{editor.dirty ? "有尚未保存的修改" : "已与持久配置一致"}</span>
          <button type="button" className="settings-button is-primary" disabled={!canSave || editor.saving || (!editor.dirty && Boolean(editor.connectionId))} onClick={onSave}>
            {editor.saving ? "保存中…" : "保存供应商"}
          </button>
        </div>
      </footer>
    </section>
  );
}

interface RuntimeFieldsProps {
  readonly editor: ConnectionEditorState;
  readonly savedAuthStatus: string;
  readonly onDraftChange: (patch: Partial<ConnectionDraft>) => void;
  readonly onFetchModels: () => void;
  readonly onWriteSecret: (kind: SecretKind, value: string) => Promise<void>;
  readonly onDeleteSecret: (kind: SecretKind) => void;
}

/** Claude 第三方字段以角色映射为核心。 */
function ClaudeConnectionFields(props: RuntimeFieldsProps & { readonly onRoleChange: (role: string, model: string) => void }) {
  return (
    <>
      <CustomEndpointFields editor={props.editor} onDraftChange={props.onDraftChange} />
      <ApiKeyField {...props} />
      <ModelFetchBlock editor={props.editor} onFetch={props.onFetchModels} />
      <section className="settings-form-block">
        <h3>Claude 角色模型</h3>
        <p>候选来自当前连接刚刚获取的模型列表；“上游可见”不等于已经通过 Trowel 能力验证。</p>
        <div className="settings-role-grid">
          {CLAUDE_ROLES.map(([role, label]) => (
            <label className="settings-field" key={role}>
              <span>{label}</span>
              <PopperSelect
                ariaLabel={`${label}模型`}
                value={props.editor.draft.claude_role_models[role] ?? UNSET_MODEL}
                options={[
                  { value: UNSET_MODEL, label: "未映射" },
                  ...props.editor.modelFetch.models.map((model) => ({ value: model, label: model })),
                ]}
                onValueChange={(value) => props.onRoleChange(role, value === UNSET_MODEL ? "" : value)}
                triggerClassName="settings-select is-wide"
                disabled={props.editor.modelFetch.status !== "ready"}
              />
            </label>
          ))}
        </div>
      </section>
    </>
  );
}

interface OfficialFieldsProps {
  readonly editor: ConnectionEditorState;
  readonly savedProxyStatus: string;
  readonly onDraftChange: (patch: Partial<ConnectionDraft>) => void;
  readonly onWriteSecret: (kind: SecretKind, value: string) => Promise<void>;
  readonly onDeleteSecret: (kind: SecretKind) => void;
  readonly onFetchModels: () => void;
  readonly onStartLogin: () => void;
  readonly onOpenLogin: (url: string) => void;
  readonly onRefreshAccount: () => void;
}

/** Codex 官方连接展示原生账号摘要；OAuth 与内部账号目录仍由 Codex/Trowel 管理。 */
function CodexOfficialFields({
  editor,
  savedProxyStatus,
  onDraftChange,
  onWriteSecret,
  onDeleteSecret,
  onFetchModels,
  onStartLogin,
  onOpenLogin,
  onRefreshAccount,
}: OfficialFieldsProps) {
  const accountState = editor.officialAccount;
  const account = accountState.account;
  const login = accountState.login;
  return (
    <>
      <section className="settings-form-block">
        <h3>ChatGPT 登录账号</h3>
        <p>每个供应商保存一套独立的 Codex 原生登录状态。账号目录和令牌不会显示在设置中。</p>
        {!editor.connectionId ? (
          <div className="settings-account-card is-empty">
            <div><strong>保存供应商后登录</strong><small>保存只创建独立账号槽，不会复用或覆盖其他账号。</small></div>
          </div>
        ) : accountState.status === "loading" && !account ? (
          <div className="settings-account-card is-empty" role="status">正在读取 Codex 登录状态…</div>
        ) : account?.status === "logged_in" ? (
          <div className="settings-account-card">
            <div>
              <strong>{account.email ?? "已登录 ChatGPT"}</strong>
              <small>{planLabel(account.plan_type)}{account.auth_mode ? ` · ${account.auth_mode}` : ""}</small>
            </div>
            <StatusPill status="available" label="已登录" />
            <button type="button" className="settings-button" disabled={accountState.loginStarting} onClick={onStartLogin}>{accountState.loginStarting ? "正在启动…" : "更换账号"}</button>
            <button type="button" className="settings-button is-quiet" onClick={onRefreshAccount}>刷新状态</button>
          </div>
        ) : (
          <div className="settings-account-card">
            <div>
              <strong>尚未登录 ChatGPT</strong>
              <small>使用 Codex 原生设备授权，不需要手填 API key 或目录。</small>
            </div>
            <StatusPill status="unknown" label="待登录" />
            <button type="button" className="settings-button is-primary" disabled={accountState.loginStarting} onClick={onStartLogin}>{accountState.loginStarting ? "正在启动…" : "使用 ChatGPT 登录"}</button>
            <button type="button" className="settings-button is-quiet" onClick={onRefreshAccount}>刷新状态</button>
          </div>
        )}
        {login && (
          <div className="settings-login-code" role="status">
            <span>浏览器打开后输入验证码</span>
            <code>{login.user_code}</code>
            <button type="button" className="settings-button" onClick={() => onOpenLogin(login.verification_url)}>打开登录页面</button>
          </div>
        )}
        {accountState.error && <p className="settings-error" role="alert">{accountState.error}</p>}
      </section>
      <section className="settings-form-block">
        <h3>可选连接代理</h3>
        <div className="settings-form-grid">
          <label className="settings-field">
            <span>代理地址</span>
            <input value={editor.draft.proxy_url ?? ""} onChange={(event) => onDraftChange({ proxy_url: nullable(event.target.value) })} placeholder="http://127.0.0.1:7890" />
          </label>
          <label className="settings-field">
            <span>代理用户名</span>
            <input value={editor.draft.proxy_username ?? ""} onChange={(event) => onDraftChange({ proxy_username: nullable(event.target.value) })} />
          </label>
        </div>
        <SecretField
          label="代理密码"
          kind="proxy_password"
          status={savedProxyStatus}
          enabled={Boolean(editor.connectionId && editor.draft.proxy_url)}
          onWrite={onWriteSecret}
          onDelete={onDeleteSecret}
        />
      </section>
      <CodexCatalogFields editor={editor} onDraftChange={onDraftChange} onFetch={onFetchModels} />
    </>
  );
}

/** Codex 第三方字段允许从当前上游目录挑选结构化 catalog。 */
function CodexCustomFields(props: RuntimeFieldsProps) {
  return (
    <>
      <CustomEndpointFields editor={props.editor} onDraftChange={props.onDraftChange} />
      <ApiKeyField {...props} />
      <CodexCatalogFields editor={props.editor} onDraftChange={props.onDraftChange} onFetch={props.onFetchModels} />
    </>
  );
}

/** Direct API 只管理协议、端点、凭据和上游目录，不伪造 Agent runtime 字段。 */
function DirectApiFields(props: RuntimeFieldsProps) {
  return (
    <>
      <section className="settings-form-block">
        <h3>调用协议</h3>
        <PopperSelect
          ariaLabel="Direct API 协议"
          value={props.editor.draft.protocol}
          options={[
            { value: "anthropic_messages", label: "Anthropic Messages" },
            { value: "openai_responses", label: "OpenAI Responses" },
          ]}
          onValueChange={(value) => props.onDraftChange({ protocol: value as ConnectionDraft["protocol"] })}
          triggerClassName="settings-select is-wide"
        />
      </section>
      <CustomEndpointFields editor={props.editor} onDraftChange={props.onDraftChange} />
      <ApiKeyField {...props} />
      <ModelFetchBlock editor={props.editor} onFetch={props.onFetchModels} />
    </>
  );
}

/** 自定义连接共用服务地址和可选模型目录覆盖。 */
function CustomEndpointFields({ editor, onDraftChange }: Pick<RuntimeFieldsProps, "editor" | "onDraftChange">) {
  return (
    <section className="settings-form-block">
      <h3>上游端点</h3>
      <div className="settings-form-grid">
        <label className="settings-field">
          <span>请求地址</span>
          <input value={editor.draft.base_url ?? ""} onChange={(event) => onDraftChange({ base_url: nullable(event.target.value) })} placeholder="https://api.example.com" />
        </label>
        <label className="settings-field">
          <span>模型列表地址（可选）</span>
          <input value={editor.draft.models_url ?? ""} onChange={(event) => onDraftChange({ models_url: nullable(event.target.value) })} placeholder="默认根据请求地址推导" />
        </label>
      </div>
    </section>
  );
}

/** 复用 API key 只写交互。 */
function ApiKeyField(props: RuntimeFieldsProps) {
  return (
    <section className="settings-form-block">
      <h3>认证凭据</h3>
      <SecretField
        label="API key"
        kind="api_key"
        status={props.savedAuthStatus}
        enabled={Boolean(props.editor.connectionId)}
        onWrite={props.onWriteSecret}
        onDelete={props.onDeleteSecret}
      />
    </section>
  );
}

interface SecretFieldProps {
  readonly label: string;
  readonly kind: SecretKind;
  readonly status: string;
  readonly enabled: boolean;
  readonly onWrite: (kind: SecretKind, value: string) => Promise<void>;
  readonly onDelete: (kind: SecretKind) => void;
}

/** 使用非受控输入让 secret 不进入 React 或 Zustand state。 */
function SecretField({ label, kind, status, enabled, onWrite, onDelete }: SecretFieldProps) {
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = event.currentTarget;
    const input = form.elements.namedItem("secret-value") as HTMLInputElement | null;
    const value = input?.value ?? "";
    form.reset();
    if (value.trim()) void onWrite(kind, value);
  };
  return (
    <form className="settings-secret" onSubmit={submit} autoComplete="off">
      <input
        className="settings-secret__username"
        name="username"
        value="trowel-connection"
        autoComplete="username"
        aria-hidden="true"
        tabIndex={-1}
        readOnly
      />
      <label className="settings-field">
        <span>{label}</span>
        <input name="secret-value" type="password" disabled={!enabled} placeholder={enabled ? "输入新值后立即保存" : "先保存供应商"} autoComplete="new-password" />
      </label>
      <StatusPill status={status === "configured" ? "available" : "unknown"} label={status === "configured" ? "已配置" : "未配置"} />
      <button type="submit" className="settings-button" disabled={!enabled}>保存新值</button>
      <button type="button" className="settings-button is-quiet" disabled={!enabled || status !== "configured"} onClick={() => onDelete(kind)}>删除</button>
    </form>
  );
}

/** 展示模型目录有限状态和重试入口。 */
function ModelFetchBlock({ editor, onFetch }: { readonly editor: ConnectionEditorState; readonly onFetch: () => void }) {
  const state = editor.modelFetch;
  const detail = modelFetchDetail(state.status, state.models.length, state.error);
  return (
    <section className="settings-form-block settings-model-fetch">
      <div>
        <h3>上游可见模型</h3>
        <p>{detail}</p>
        {state.sourceEndpoint && <code>{state.sourceEndpoint}</code>}
      </div>
      <button type="button" className="settings-button" disabled={!editor.connectionId || state.status === "loading"} onClick={onFetch}>
        {state.status === "loading" ? "获取中…" : state.status === "error" || state.status === "stale" ? "重新获取" : "获取可用模型"}
      </button>
    </section>
  );
}

/** Codex catalog 只允许勾选本次 ready 目录中的模型。 */
function CodexCatalogFields({ editor, onDraftChange, onFetch }: Pick<RuntimeFieldsProps, "editor" | "onDraftChange"> & { readonly onFetch: () => void }) {
  const selectModel = (index: number, model: string) => {
    const current = editor.draft.codex_catalog[index];
    const nativeEntry = editor.modelFetch.codexCatalog.find((item) => item.id === model);
    const entry: CodexCatalogEntry = nativeEntry ?? {
      id: model,
      display_name: current?.display_name ?? null,
      default_effort: current?.default_effort ?? "high",
      supported_efforts: current?.supported_efforts.length ? current.supported_efforts : [...EFFORTS],
    };
    const next = editor.draft.codex_catalog.filter((item, itemIndex) => itemIndex !== index && item.id !== model);
    next.splice(Math.min(index, next.length), 0, entry);
    onDraftChange({ codex_catalog: next, catalog_request_identity: editor.modelFetch.requestIdentity });
  };
  const updateEffort = (index: number, effort: string) => {
    onDraftChange({
      codex_catalog: editor.draft.codex_catalog.map((item, itemIndex) => itemIndex === index ? { ...item, default_effort: effort } : item),
      catalog_request_identity: editor.modelFetch.requestIdentity,
    });
  };
  const removeModel = (index: number) => onDraftChange({
    codex_catalog: editor.draft.codex_catalog.filter((_, itemIndex) => itemIndex !== index),
    catalog_request_identity: editor.modelFetch.requestIdentity,
  });
  const rows = editor.draft.codex_catalog.length
    ? editor.draft.codex_catalog
    : [null];
  const canAdd = editor.modelFetch.status === "ready" &&
    editor.modelFetch.models.some((model) => !editor.draft.codex_catalog.some((item) => item.id === model));
  return (
    <section className="settings-form-block settings-codex-catalog">
      <h3>Codex 模型 catalog</h3>
      <p>获取只刷新候选模型，不会自动全选；新会话按这里保存的模型及顺序展示。</p>
      <div className="settings-codex-catalog-list">
        {rows.map((entry, index) => (
          <div className="settings-codex-catalog-row" key={entry?.id ?? "empty-catalog-row"}>
            <PopperSelect
              ariaLabel={`Codex 模型 ${index + 1}`}
              value={entry?.id ?? UNSET_MODEL}
              options={[
                {
                  value: UNSET_MODEL,
                  label: entry ? "移除这个模型" : editor.modelFetch.status === "ready" ? "选择模型" : "获取后选择模型",
                  disabled: !entry,
                },
                ...(entry && !editor.modelFetch.models.includes(entry.id)
                  ? [{ value: entry.id, label: `${entry.id} · 已保存`, disabled: true }]
                  : []),
                ...editor.modelFetch.models
                  .filter((model) => model === entry?.id || !editor.draft.codex_catalog.some((item) => item.id === model))
                  .map((model) => ({ value: model, label: model })),
              ]}
              onValueChange={(model) => model === UNSET_MODEL ? removeModel(index) : selectModel(index, model)}
              triggerClassName="settings-codex-model-select"
              disabled={editor.modelFetch.status !== "ready"}
            />
            <PopperSelect
              ariaLabel={`Codex 模型 ${index + 1}思考强度`}
              value={entry?.default_effort ?? "high"}
              options={(entry?.supported_efforts.length ? entry.supported_efforts : EFFORTS).map((effort) => ({ value: effort, label: effort }))}
              onValueChange={(effort) => updateEffort(index, effort)}
              triggerClassName="settings-codex-effort-select"
              disabled={!entry}
            />
            {index === 0 ? (
              <button type="button" className="settings-icon-button settings-fetch-models" aria-label="获取可用模型" title="获取可用模型" disabled={!editor.connectionId || editor.modelFetch.status === "loading"} onClick={onFetch}>
                <DownloadIcon loading={editor.modelFetch.status === "loading"} />
              </button>
            ) : (
              <button type="button" className="settings-icon-button" aria-label={`移除模型 ${entry?.id ?? ""}`} onClick={() => removeModel(index)}><CloseIcon /></button>
            )}
          </div>
        ))}
        {editor.draft.codex_catalog.length > 0 && canAdd && (
          <div className="settings-codex-catalog-row is-add-row">
            <PopperSelect
              ariaLabel="添加 Codex 模型"
              value={UNSET_MODEL}
              options={[
                { value: UNSET_MODEL, label: "添加另一个模型", disabled: true },
                ...editor.modelFetch.models.filter((model) => !editor.draft.codex_catalog.some((item) => item.id === model)).map((model) => ({ value: model, label: model })),
              ]}
              onValueChange={(model) => selectModel(editor.draft.codex_catalog.length, model)}
              triggerClassName="settings-codex-model-select"
            />
          </div>
        )}
      </div>
      <small>{modelFetchDetail(editor.modelFetch.status, editor.modelFetch.models.length, editor.modelFetch.error)}</small>
    </section>
  );
}

/** 根据当前草稿即时生成与后端格式一致的脱敏预览。 */
function Preview({ editor, authStatus }: { readonly editor: ConnectionEditorState; readonly authStatus: string }) {
  const preview = buildConnectionPreview(editor, authStatus);
  return (
    <section className="settings-form-block settings-preview">
      <h3>只读脱敏 {preview.format} 预览</h3>
      <pre>{preview.text}</pre>
    </section>
  );
}

/** 将空白输入收敛为接口使用的 null。 */
function nullable(value: string): string | null {
  return value.trim() ? value : null;
}

function runtimeLabel(runtime: Connection["runtime"]): string {
  if (runtime === "claude_code") return "Claude Code";
  if (runtime === "codex") return "Codex";
  return "Direct API";
}

function lastChoiceLabel(connection: Connection): string {
  const model = connection.last_session_choice?.model;
  const effort = connection.last_session_choice?.effort;
  return model ? `上次：${model}${effort ? ` · ${effort}` : ""}` : "尚无成功会话选择";
}

function connectionDetail(connection: Connection): string {
  if (connection.kind === "claude_compatible") {
    const mapped = Object.keys(connection.claude_role_models).length;
    return mapped ? `${mapped} 项 Claude 角色已映射` : "Claude 角色模型尚未映射";
  }
  if (connection.kind === "codex_official") {
    return connection.auth.status === "referenced" ? "ChatGPT 账号已登录" : "ChatGPT 账号待登录";
  }
  if (connection.kind === "codex_custom") {
    return connection.codex_catalog.length ? `${connection.codex_catalog.length} 个 catalog 模型` : "模型 catalog 尚未配置";
  }
  return "与普通 Agent 模型连接隔离";
}

/** 连接列表只展示用户能理解的供应商入口，不泄露 Official 内部目录。 */
function connectionProviderLabel(connection: Connection): string {
  if (connection.kind === "codex_official") return "OpenAI ChatGPT";
  return connection.upstream_host ?? "尚未填写地址";
}

/** 把 Codex 返回的套餐标识改成设置页展示文本。 */
function planLabel(planType: string | null): string {
  if (!planType) return "ChatGPT 套餐未知";
  return planType.charAt(0).toUpperCase() + planType.slice(1);
}

/** 翻译连接类型。 */
function connectionKindLabel(kind: ConnectionKind): string {
  const labels: Readonly<Record<ConnectionKind, string>> = {
    claude_compatible: "Claude 第三方",
    codex_official: "Codex 官方",
    codex_custom: "Codex 第三方",
    direct_api: "Direct API",
  };
  return labels[kind];
}

/** 翻译脱敏认证状态。 */
function authStatusLabel(status: string): string {
  return status === "configured" ? "凭据已配置" : status === "referenced" ? "原生登录引用" : "缺少凭据";
}

/** 解释模型获取有限状态，不把空结果和错误混为一类。 */
function modelFetchDetail(status: ConnectionEditorState["modelFetch"]["status"], count: number, error: string | null): string {
  if (status === "loading") return "正在使用后端保存的凭据读取当前上游目录。";
  if (status === "ready") return count ? `已获取 ${count} 个上游可见模型；能力资格仍以会话配置的真实 Gate 为准。` : "上游返回了空模型列表。";
  if (status === "error") return error ?? "模型列表获取失败，可以修正地址或凭据后重试。";
  if (status === "stale") return "地址、协议或凭据身份已变化，旧候选已经失效。";
  return "保存供应商和凭据后获取模型列表。";
}

function PlusIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14" /></svg>;
}

function SlidersIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h10M18 7h2M4 17h2M10 17h10M14 4v6M6 14v6" /></svg>;
}

function DownloadIcon({ loading }: { readonly loading: boolean }) {
  return <svg className={loading ? "is-spinning" : ""} viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v12M7 10l5 5 5-5M5 20h14" /></svg>;
}

function CloseIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18" /></svg>;
}
