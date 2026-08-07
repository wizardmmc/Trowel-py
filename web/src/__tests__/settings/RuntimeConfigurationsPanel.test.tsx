/** 验证运行配置编辑器的可选别名和 Runtime 专属思考强度。 */

import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import type {
  ConfigurationCatalog,
  Connection,
  SessionConfigurationEditorState,
} from "../../settings/domain/types";
import { RuntimeConfigurationsPanel } from "../../settings/ui/RuntimeConfigurationsPanel";

const claudeConnection: Connection = {
  id: "claude-1",
  version: 3,
  identity_version: 2,
  name: "GLM Claude",
  runtime: "claude_code",
  kind: "claude_compatible",
  protocol: "anthropic_messages",
  base_url: "https://provider.example/v1",
  models_url: null,
  upstream_host: "provider.example",
  auth: { kind: "api_key", status: "configured" },
  login_directory: null,
  login_directory_exists: true,
  claude_config_inherited: true,
  codex_config_inherited: null,
  proxy: { url: null, username: null, password_status: "missing" },
  claude_role_models: { opus: "glm-5.2" },
  codex_catalog: [],
  catalog: {
    status: "ready",
    models: ["glm-5.2"],
    source_endpoint: "https://provider.example/v1/models",
    fetched_at: "2026-08-07T00:00:00Z",
    request_identity: "request-1",
    error_code: null,
  },
  validation_status: "ready",
  capability_version: "m13-l01-v1",
  last_session_choice: null,
  secret_versions: { api_key: 1 },
  preview: {},
};

const catalog: ConfigurationCatalog = {
  connections: [claudeConnection],
  session_configurations: [],
  task_bindings: [],
  agent_defaults: {
    version: 0,
    session_configuration_id: null,
    permission: null,
    memory_enabled: true,
    profile_enabled: true,
    self_enabled: true,
  },
};

/** 创建字段完整、可保存的 Claude 运行配置编辑态。 */
function editor(
  patch: Partial<SessionConfigurationEditorState["draft"]> = {},
): SessionConfigurationEditorState {
  return {
    configurationId: null,
    version: 0,
    draft: {
      name: "Claude Max",
      connection_id: claudeConnection.id,
      model: "opus",
      effort: null,
      stable_alias: null,
      agent_callable: false,
      ...patch,
    },
    dirty: true,
    saving: false,
    archiving: false,
    error: null,
    conflict: false,
  };
}

it("allows saving without an alias and exposes every Claude CLI effort", async () => {
  render(
    <RuntimeConfigurationsPanel
      catalog={catalog}
      editor={editor()}
      onOpen={vi.fn()}
      onCreate={vi.fn()}
      onClose={vi.fn()}
      onChange={vi.fn()}
      onSave={vi.fn()}
      onArchive={vi.fn()}
    />,
  );

  expect(screen.getByRole("button", { name: "保存配置" })).toBeEnabled();
  expect(screen.getByRole("switch", { name: "允许 Agent 调用" })).toBeDisabled();

  await userEvent.click(screen.getByRole("combobox", { name: "运行配置思考强度" }));
  for (const effort of ["low", "medium", "high", "xhigh", "max"]) {
    expect(screen.getByRole("option", { name: effort })).toBeInTheDocument();
  }
});

it("turns off Agent calling when the optional alias is cleared", () => {
  const onChange = vi.fn();
  render(
    <RuntimeConfigurationsPanel
      catalog={catalog}
      editor={editor({ stable_alias: "claude-main", agent_callable: true })}
      onOpen={vi.fn()}
      onCreate={vi.fn()}
      onClose={vi.fn()}
      onChange={onChange}
      onSave={vi.fn()}
      onArchive={vi.fn()}
    />,
  );

  expect(screen.getByRole("switch", { name: "允许 Agent 调用" })).toBeEnabled();
  fireEvent.change(screen.getByPlaceholderText("例如：codex1"), {
    target: { value: "" },
  });

  expect(onChange).toHaveBeenCalledWith({
    stable_alias: null,
    agent_callable: false,
  });
});
