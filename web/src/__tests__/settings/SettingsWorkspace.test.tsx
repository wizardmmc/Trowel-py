/** 验证生产设置工作区使用同一状态容器切换六组真实页面。 */

import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it } from "vitest";
import { createSettingsStore } from "../../settings/application/store";
import type { ConfigurationCatalog } from "../../settings/domain/types";
import { SettingsWorkspace } from "../../settings/ui/SettingsWorkspace";

const emptyCatalog: ConfigurationCatalog = {
  connections: [],
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

it("switches between the six settings groups without leaving the workspace", async () => {
  const store = createSettingsStore();
  store.setState({
    initialized: true,
    catalog: emptyCatalog,
    paths: { data_mode: "browser", paths: {} },
    diagnostics: { connections: [] },
  });
  render(<SettingsWorkspace store={store} />);

  expect(screen.getByRole("heading", { name: "存储与路径" })).toBeInTheDocument();

  await userEvent.click(screen.getByRole("button", { name: /模型连接/ }));
  expect(screen.getByRole("heading", { name: "模型连接" })).toBeInTheDocument();

  await userEvent.click(screen.getByRole("button", { name: /后台任务/ }));
  expect(screen.getByRole("heading", { name: "后台任务" })).toBeInTheDocument();
  expect(screen.getAllByRole("combobox")).toHaveLength(5);
  expect(screen.getAllByRole("switch")).toHaveLength(5);
  expect(screen.queryByRole("button", { name: "保存" })).not.toBeInTheDocument();

  await userEvent.click(screen.getByRole("button", { name: /Agent 默认/ }));
  expect(screen.getByRole("heading", { name: "Agent 默认条件" })).toBeInTheDocument();
  expect(screen.getByText("默认 runtime")).toBeInTheDocument();
  expect(screen.getByText("默认模型连接")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /保存默认条件/ })).not.toBeInTheDocument();

  await userEvent.click(screen.getByRole("button", { name: /连接诊断/ }));
  expect(screen.getByRole("heading", { name: "连接诊断" })).toBeInTheDocument();

  await userEvent.click(screen.getByRole("button", { name: /关于/ }));
  expect(screen.getByRole("heading", { name: "关于 Trowel" })).toBeInTheDocument();
  expect(screen.getByText("development · 浏览器开发环境")).toBeInTheDocument();
  expect(screen.getByText("本地优先")).toBeInTheDocument();
});

it("reveals a distinct connection-kind step after choosing Codex", async () => {
  const store = createSettingsStore();
  store.setState({
    initialized: true,
    catalog: emptyCatalog,
    paths: { data_mode: "browser", paths: {} },
  });
  render(<SettingsWorkspace store={store} active={false} />);

  await userEvent.click(screen.getByRole("button", { name: /模型连接/ }));
  await userEvent.click(screen.getByRole("button", { name: /Codex · 0/ }));
  await userEvent.click(screen.getByRole("button", { name: "添加连接" }));

  expect(screen.getByRole("heading", { name: "1. 选择 Runtime" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "2. 连接种类" })).toBeInTheDocument();
  const kinds = screen.getByRole("group", { name: "Codex 连接种类" });
  expect(within(kinds).getByRole("button", { name: /OpenAI Official/ })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await userEvent.click(within(kinds).getByRole("button", { name: /第三方 Responses/ }));
  expect(within(kinds).getByRole("button", { name: /第三方 Responses/ })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  expect(screen.getByText("Codex 模型 catalog")).toBeInTheDocument();
});

it("shows the real connection registry path in the mockup path group", () => {
  const store = createSettingsStore();
  store.setState({
    initialized: true,
    catalog: emptyCatalog,
    paths: {
      data_mode: "isolated-dev",
      paths: {
        connection_registry: {
          path: "/tmp/trowel/trowel.db",
          exists: true,
          kind: "file",
        },
      },
    },
  });

  render(<SettingsWorkspace store={store} active={false} />);

  const registry = screen.getByText("连接注册表").closest(".settings-path-row");
  expect(registry).not.toBeNull();
  expect(within(registry as HTMLElement).getByText("/tmp/trowel/trowel.db")).toBeInTheDocument();
});

it("keeps the mockup top shell and a dedicated Electron drag strip", async () => {
  const store = createSettingsStore();
  store.setState({
    initialized: true,
    catalog: emptyCatalog,
    paths: { data_mode: "browser", paths: {} },
    diagnostics: { connections: [] },
  });
  const { container } = render(<SettingsWorkspace store={store} active={false} />);

  expect(container.querySelector(".settings-drag-region")).toHaveTextContent("Trowel");
  expect(container.querySelector(".settings-surface-topbar")).toHaveTextContent("存储与路径");
  expect(container.querySelector(".settings-sidebar__status-dot")).toHaveClass("is-ready");

  await userEvent.click(screen.getByRole("button", { name: /模型连接/ }));
  expect(container.querySelector(".settings-surface-topbar")).toHaveTextContent("模型连接");
});

it("does not present a failed settings read as healthy", () => {
  const store = createSettingsStore();
  store.setState({ initialized: true, error: "配置读取失败" });

  const { container } = render(<SettingsWorkspace store={store} active={false} />);

  expect(container.querySelector(".settings-sidebar__summary")).toHaveTextContent("配置读取失败");
  expect(container.querySelector(".settings-sidebar__status-dot")).toHaveClass("is-error");
});

it("marks a persisted task binding stale when its configuration lacks that task capability", async () => {
  const weeklyOnly = {
    id: "weekly-only",
    version: 1,
    name: "只允许 Weekly",
    runtime: "direct_api" as const,
    connection_id: "direct-1",
    connection_identity_version: 1,
    model: "glm-5.2",
    effort: null,
    capability: {
      status: "verified",
      version: "m13-l01-v1",
      source: "real_gate",
      eligible_tasks: ["memory_weekly" as const],
    },
    availability: "available",
    disabled_reason: null,
  };
  const store = createSettingsStore();
  store.setState({
    initialized: true,
    catalog: {
      ...emptyCatalog,
      session_configurations: [weeklyOnly],
      task_bindings: [
        {
          task_id: "memory_daily",
          version: 2,
          session_configuration_id: weeklyOnly.id,
        },
      ],
    },
  });
  render(<SettingsWorkspace store={store} />);

  await userEvent.click(screen.getByRole("button", { name: /后台任务/ }));

  expect(screen.getByText("原绑定已失效")).toBeInTheDocument();
});

it("renders the Codex custom catalog as anchored model, effort, and fetch controls", async () => {
  const store = createSettingsStore();
  store.setState({
    initialized: true,
    catalog: {
      ...emptyCatalog,
      connections: [{
        id: "codex-custom-1",
        version: 3,
        identity_version: 2,
        name: "DeepSeek Responses",
        runtime: "codex",
        kind: "codex_custom",
        protocol: "openai_responses",
        base_url: "https://api.deepseek.com/v1",
        models_url: null,
        upstream_host: "api.deepseek.com",
        auth: { kind: "api_key", status: "configured" },
        login_directory: null,
        login_directory_exists: null,
        proxy: { url: null, username: null, password_status: "missing" },
        claude_role_models: {},
        codex_catalog: [{
          id: "deepseek-reasoner",
          display_name: null,
          default_effort: "high",
          supported_efforts: ["low", "medium", "high"],
        }],
        catalog: {
          status: "ready",
          models: ["deepseek-chat", "deepseek-reasoner"],
          source_endpoint: "https://api.deepseek.com/models",
          fetched_at: "2026-08-05T00:00:00Z",
          request_identity: "request-1",
          error_code: null,
        },
        validation_status: "incomplete",
        capability_version: "m13-l01-v1",
        last_session_choice: null,
        secret_versions: { api_key: 1, proxy_password: 0 },
        preview: {},
      }],
    },
  });
  render(<SettingsWorkspace store={store} active={false} />);

  await userEvent.click(screen.getByRole("button", { name: /模型连接/ }));
  await userEvent.click(screen.getByRole("button", { name: /Codex · 1/ }));
  await userEvent.click(screen.getByRole("button", { name: /DeepSeek Responses/ }));

  expect(screen.getByRole("combobox", { name: "Codex 模型 1" })).toHaveClass("settings-codex-model-select");
  expect(screen.getByRole("combobox", { name: "Codex 模型 1思考强度" })).toHaveClass("settings-codex-effort-select");
  expect(screen.getByRole("button", { name: "获取可用模型" })).toHaveClass("settings-fetch-models");
});

it("shows an Official account instead of asking for an internal login directory", async () => {
  const store = createSettingsStore({
    fetchCodexOfficialAccount: async () => ({
      status: "logged_in",
      email: "user@example.com",
      plan_type: "pro",
      auth_mode: "chatgpt",
    }),
  });
  store.setState({
    initialized: true,
    activeSection: "connections",
    catalog: {
      ...emptyCatalog,
      connections: [{
        id: "official-1",
        version: 1,
        identity_version: 1,
        name: "OpenAI Pro x20",
        runtime: "codex",
        kind: "codex_official",
        protocol: "codex_official",
        base_url: null,
        models_url: null,
        upstream_host: null,
        auth: { kind: "oauth_reference", status: "referenced" },
        login_directory: null,
        login_directory_exists: true,
        proxy: { url: null, username: null, password_status: "missing" },
        claude_role_models: {},
        codex_catalog: [],
        catalog: {
          status: "idle",
          models: [],
          source_endpoint: null,
          fetched_at: null,
          request_identity: null,
          error_code: null,
        },
        validation_status: "unknown",
        capability_version: "m13-l01-v1",
        last_session_choice: null,
        secret_versions: {},
        preview: {},
      }],
    },
  });
  render(<SettingsWorkspace store={store} active={false} />);

  await userEvent.click(screen.getByRole("button", { name: /Codex · 1/ }));
  await userEvent.click(screen.getByRole("button", { name: /OpenAI Pro x20/ }));

  expect(await screen.findByText("user@example.com")).toBeInTheDocument();
  expect(screen.getByText(/^Pro · chatgpt$/)).toBeInTheDocument();
  expect(screen.queryByText("登录目录")).not.toBeInTheDocument();
  expect(screen.getByText("供应商名称")).toBeInTheDocument();
  expect(screen.getByText("Codex 模型 catalog")).toBeInTheDocument();
});
