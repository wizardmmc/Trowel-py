/** 验证设置状态只接受当前连接身份的模型结果，并严格按任务能力筛选。 */

import { describe, expect, it, vi } from "vitest";
import { createSettingsStore } from "../../settings/application/store";
import {
  eligibleAgentSessionConfigurations,
  eligibleSessionConfigurations,
} from "../../settings/application/selectors";
import type {
  AgentDefaults,
  ConfigurationCatalog,
  FetchModelsResult,
  SecretStatusResult,
} from "../../settings/domain/types";
import { ConfigurationApiError } from "../../settings/transport/api";

const catalog: ConfigurationCatalog = {
  connections: [
    {
      id: "connection-1",
      version: 3,
      identity_version: 2,
      name: "GLM Claude",
      runtime: "claude_code",
      kind: "claude_compatible",
      protocol: "anthropic_messages",
      base_url: "https://old.example.com",
      models_url: null,
      upstream_host: "old.example.com",
      auth: { kind: "api_key", status: "configured" },
      login_directory: null,
      login_directory_exists: null,
      claude_config_inherited: false,
      codex_config_inherited: null,
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
      validation_status: "incomplete",
      capability_version: "m13-l01-v1",
      last_session_choice: null,
      secret_versions: { api_key: 1, proxy_password: 0 },
      preview: {},
    },
  ],
  session_configurations: [
    {
      id: "weekly-only",
      version: 1,
      name: "GLM Weekly",
      runtime: "direct_api",
      connection_id: "connection-1",
      connection_identity_version: 2,
      model: "glm-5.2",
      effort: null,
      capability: {
        status: "verified",
        version: "m13-l01-v1",
        source: "real_gate",
        eligible_tasks: ["memory_weekly"],
      },
      availability: "available",
      disabled_reason: null,
    },
    {
      id: "stale-weekly",
      version: 1,
      name: "旧 Weekly",
      runtime: "direct_api",
      connection_id: "connection-1",
      connection_identity_version: 1,
      model: "glm-5.2",
      effort: null,
      capability: {
        status: "verified",
        version: "m13-l01-v1",
        source: "real_gate",
        eligible_tasks: ["memory_weekly"],
      },
      availability: "stale",
      disabled_reason: "CONNECTION_IDENTITY_CHANGED",
    },
  ],
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

/** 创建可由测试精确控制完成时刻的 Promise。 */
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((complete) => {
    resolve = complete;
  });
  return { promise, resolve };
}

describe("settings store", () => {
  it("只在已保存且无脏修改时继承 Claude 全局配置", async () => {
    const inherit = vi.fn().mockResolvedValue({
      ...catalog.connections[0],
      claude_config_inherited: true,
    });
    const store = createSettingsStore({ inheritGlobalClaudeConfig: inherit });
    store.setState({ catalog });
    store.getState().openConnection("connection-1");

    await store.getState().inheritRuntimeConfig();

    expect(inherit).toHaveBeenCalledWith("connection-1", 3);
    expect(
      store.getState().catalog?.connections[0].claude_config_inherited,
    ).toBe(true);
    expect(store.getState().connectionEditor?.inheritingRuntimeConfig).toBe(false);

    store.getState().updateConnectionDraft({ name: "尚未保存" });
    await store.getState().inheritRuntimeConfig();
    expect(inherit).toHaveBeenCalledTimes(1);
  });

  it("Codex 连接使用自己的全局复制端点", async () => {
    const codexConnection = {
      ...catalog.connections[0],
      runtime: "codex" as const,
      kind: "codex_custom" as const,
      protocol: "openai_responses" as const,
      claude_config_inherited: null,
      codex_config_inherited: false,
    };
    const codexCatalog = { ...catalog, connections: [codexConnection] };
    const inheritCodex = vi.fn().mockResolvedValue({
      ...codexConnection,
      codex_config_inherited: true,
    });
    const inheritClaude = vi.fn();
    const store = createSettingsStore({
      inheritGlobalCodexConfig: inheritCodex,
      inheritGlobalClaudeConfig: inheritClaude,
    });
    store.setState({ catalog: codexCatalog });
    store.getState().openConnection("connection-1");

    await store.getState().inheritRuntimeConfig();

    expect(inheritCodex).toHaveBeenCalledWith("connection-1", 3);
    expect(inheritClaude).not.toHaveBeenCalled();
    expect(store.getState().catalog?.connections[0].codex_config_inherited).toBe(true);
  });

  it("复制期间切换连接时仍按原请求结果返回失败", async () => {
    let rejectInheritance!: (error: Error) => void;
    const inherit = vi.fn(
      () =>
        new Promise<(typeof catalog.connections)[number]>((_resolve, reject) => {
          rejectInheritance = reject;
        }),
    );
    const second = {
      ...catalog.connections[0],
      id: "connection-2",
      name: "另一个连接",
    };
    const store = createSettingsStore({ inheritGlobalClaudeConfig: inherit });
    store.setState({
      catalog: { ...catalog, connections: [catalog.connections[0], second] },
    });
    store.getState().openConnection("connection-1");

    const pending = store.getState().inheritRuntimeConfig();
    store.getState().openConnection("connection-2");
    rejectInheritance(new Error("复制失败"));

    await expect(pending).resolves.toBe(false);
    expect(store.getState().connectionEditor?.connectionId).toBe("connection-2");
    expect(store.getState().connectionEditor?.error).toBeNull();
  });

  it("诊断失败时仍保留已经成功的配置与路径", async () => {
    const store = createSettingsStore({
      fetchCatalog: vi.fn().mockResolvedValue(catalog),
      fetchPaths: vi.fn().mockResolvedValue({
        data_mode: "isolated-dev",
        paths: {
          data_root: { path: "/tmp/trowel", exists: true, kind: "directory" },
        },
      }),
      fetchDiagnostics: vi.fn().mockRejectedValue(new Error("诊断暂时不可用")),
    });

    await store.getState().initialize();

    expect(store.getState().catalog).toBe(catalog);
    expect(store.getState().paths?.paths.data_root.path).toBe("/tmp/trowel");
    expect(store.getState().diagnostics).toBeNull();
    expect(store.getState().diagnosticsError).toBe("诊断暂时不可用");
  });

  it("只让真实 allowlist 且当前可用的配置进入任务选择器", () => {
    expect(
      eligibleSessionConfigurations(catalog, "memory_weekly").map(
        (configuration) => configuration.id,
      ),
    ).toEqual(["weekly-only"]);
    expect(eligibleSessionConfigurations(catalog, "memory_daily")).toEqual([]);
  });

  it("Claude 与 Codex 原生 Agent 配置都能进入后台任务选择器", () => {
    const managedConfigurations = [
      {
        ...catalog.session_configurations[0],
        id: "glm-agent",
        runtime: "claude_code" as const,
        capability: {
          ...catalog.session_configurations[0].capability,
          eligible_tasks: ["memory_daily" as const],
        },
      },
      {
        ...catalog.session_configurations[0],
        id: "luna-agent",
        runtime: "codex" as const,
        model: "gpt-5.6-luna",
        capability: {
          ...catalog.session_configurations[0].capability,
          eligible_tasks: ["memory_daily" as const],
        },
      },
    ];

    expect(
      eligibleSessionConfigurations(
        { ...catalog, session_configurations: managedConfigurations },
        "memory_daily",
      ).map((configuration) => configuration.id),
    ).toEqual(["glm-agent", "luna-agent"]);
  });

  it("Agent 默认不提供仅供后台任务使用的 direct API 配置", () => {
    expect(eligibleAgentSessionConfigurations(catalog)).toEqual([]);
  });

  it("连接身份变化后丢弃晚到的旧模型列表", async () => {
    let resolveModels!: (value: FetchModelsResult) => void;
    const fetchModels = vi.fn(
      () =>
        new Promise<FetchModelsResult>((resolve) => {
          resolveModels = resolve;
        }),
    );
    const store = createSettingsStore({ fetchModels });
    store.setState({ catalog });
    store.getState().openConnection("connection-1");

    const pending = store.getState().fetchConnectionModels();
    store.getState().updateConnectionDraft({
      base_url: "https://new.example.com",
    });
    resolveModels({
      status: "ready",
      models: ["old-model"],
      source_endpoint: "https://old.example.com/v1/models",
      fetched_at: "2026-08-04T12:00:00Z",
      request_identity: "old-request",
      connection_version: 4,
      codex_catalog: [],
    });
    await pending;

    expect(store.getState().connectionEditor?.modelFetch.status).toBe("stale");
    expect(store.getState().connectionEditor?.modelFetch.models).toEqual([]);
  });

  it("Codex 获取模型只刷新候选和元数据，不自动增加或重排已选模型", async () => {
    const codexConnection = {
      ...catalog.connections[0],
      runtime: "codex" as const,
      kind: "codex_custom" as const,
      protocol: "openai_responses" as const,
      name: "Codex Lab",
      codex_catalog: [
        {
          id: "gpt-5.6-terra",
          display_name: null,
          default_effort: "high",
          supported_efforts: ["high"],
        },
        {
          id: "gpt-5.6-sol",
          display_name: null,
          default_effort: "high",
          supported_efforts: ["high"],
        },
      ],
      catalog: {
        ...catalog.connections[0].catalog,
        status: "ready",
        models: ["codex-auto-review", "gpt-5.6-sol", "gpt-image-1"],
      },
    };
    const store = createSettingsStore({
      fetchModels: vi.fn().mockResolvedValue({
        status: "ready",
        models: ["gpt-image-1", "gpt-5.6-terra", "gpt-5.6-sol"],
        source_endpoint: "https://lab.example/v1/models",
        fetched_at: "2026-08-06T12:00:00Z",
        request_identity: "codex-request",
        connection_version: 4,
        codex_catalog: [
          { id: "gpt-5.6-sol", display_name: null, default_effort: "low", supported_efforts: ["low", "medium", "high", "xhigh", "max", "ultra"] },
          { id: "gpt-5.6-terra", display_name: null, default_effort: "medium", supported_efforts: ["low", "medium", "high", "xhigh", "max", "ultra"] },
        ],
      }),
    });
    store.setState({ catalog: { ...catalog, connections: [codexConnection] } });
    store.getState().openConnection("connection-1");
    expect(store.getState().connectionEditor?.modelFetch.models).toEqual([
      "gpt-5.6-terra",
      "gpt-5.6-sol",
    ]);

    await store.getState().fetchConnectionModels();

    const editor = store.getState().connectionEditor;
    expect(editor?.modelFetch.models).toEqual(["gpt-5.6-sol", "gpt-5.6-terra"]);
    expect(editor?.draft.codex_catalog.map((entry) => entry.id)).toEqual([
      "gpt-5.6-terra",
      "gpt-5.6-sol",
    ]);
    expect(editor?.draft.codex_catalog[1]).toMatchObject({
      id: "gpt-5.6-sol",
      default_effort: "high",
      supported_efforts: ["low", "medium", "high", "xhigh", "max", "ultra"],
    });
    expect(editor?.dirty).toBe(true);
  });

  it("Codex 原生目录为空时保留已选模型，等待用户明确处理", async () => {
    const codexConnection = {
      ...catalog.connections[0],
      runtime: "codex" as const,
      kind: "codex_custom" as const,
      protocol: "openai_responses" as const,
      name: "Codex Lab",
      codex_catalog: [
        {
          id: "gpt-5.6-sol",
          display_name: null,
          default_effort: "high",
          supported_efforts: ["high"],
        },
      ],
      catalog: {
        ...catalog.connections[0].catalog,
        status: "ready",
        models: ["gpt-5.6-sol"],
      },
    };
    const store = createSettingsStore({
      fetchModels: vi.fn().mockResolvedValue({
        status: "ready",
        models: ["provider-custom-model"],
        source_endpoint: "https://lab.example/v1/models",
        fetched_at: "2026-08-06T12:00:00Z",
        request_identity: "empty-native-request",
        connection_version: 4,
        codex_catalog: [],
      }),
    });
    store.setState({ catalog: { ...catalog, connections: [codexConnection] } });
    store.getState().openConnection("connection-1");

    await store.getState().fetchConnectionModels();

    const editor = store.getState().connectionEditor;
    expect(editor?.draft.codex_catalog.map((entry) => entry.id)).toEqual([
      "gpt-5.6-sol",
    ]);
    expect(editor?.draft.catalog_request_identity).toBe("empty-native-request");
    expect(editor?.modelFetch.models).toEqual([]);
    expect(editor?.dirty).toBe(false);
  });

  it("只写凭据不会进入可序列化前端状态", async () => {
    const writeSecret = vi.fn().mockResolvedValue({
      connection_id: "connection-1",
      version: 4,
      status: "configured",
    });
    const store = createSettingsStore({ writeSecret });
    store.setState({ catalog });
    store.getState().openConnection("connection-1");

    await store.getState().writeConnectionSecret("api_key", "secret-canary");

    expect(writeSecret).toHaveBeenCalledWith(
      "connection-1",
      "api_key",
      3,
      "secret-canary",
    );
    expect(JSON.stringify(store.getState())).not.toContain("secret-canary");
  });

  it("旧连接的凭据响应不会改写后来打开的连接编辑器", async () => {
    const pendingSecret = deferred<SecretStatusResult>();
    const secondConnection = {
      ...catalog.connections[0],
      id: "connection-2",
      version: 9,
      name: "后来打开的连接",
    };
    const store = createSettingsStore({
      writeSecret: vi.fn().mockReturnValue(pendingSecret.promise),
    });
    store.setState({
      catalog: { ...catalog, connections: [...catalog.connections, secondConnection] },
    });
    store.getState().openConnection("connection-1");

    const pending = store.getState().writeConnectionSecret("api_key", "secret-canary");
    store.getState().openConnection("connection-2");
    pendingSecret.resolve({
      connection_id: "connection-1",
      version: 4,
      status: "configured",
    });
    await pending;

    expect(store.getState().connectionEditor?.connectionId).toBe("connection-2");
    expect(store.getState().connectionEditor?.version).toBe(9);
    expect(store.getState().connectionEditor?.draft.name).toBe("后来打开的连接");
  });

  it("连接保存返回时保留用户在等待期间继续修改的草稿", async () => {
    const pendingUpdate = deferred<(typeof catalog.connections)[number]>();
    const store = createSettingsStore({
      updateConnection: vi.fn().mockReturnValue(pendingUpdate.promise),
    });
    store.setState({ catalog });
    store.getState().openConnection("connection-1");
    store.getState().updateConnectionDraft({ name: "提交版本" });

    const pending = store.getState().saveConnection();
    store.getState().updateConnectionDraft({ name: "等待期间的新修改" });
    pendingUpdate.resolve({
      ...catalog.connections[0],
      version: 4,
      name: "提交版本",
    });
    await pending;

    expect(store.getState().connectionEditor?.version).toBe(4);
    expect(store.getState().connectionEditor?.draft.name).toBe("等待期间的新修改");
    expect(store.getState().connectionEditor?.dirty).toBe(true);
    expect(store.getState().connectionEditor?.saving).toBe(false);
  });

  it("Official 账号状态与登录引导保存在当前供应商编辑器中", async () => {
    const official = {
      ...catalog.connections[0],
      id: "official-1",
      name: "OpenAI Pro",
      runtime: "codex" as const,
      kind: "codex_official" as const,
      protocol: "codex_official" as const,
      base_url: null,
      upstream_host: null,
      auth: { kind: "oauth_reference", status: "missing" },
      codex_catalog: [],
    };
    const store = createSettingsStore({
      fetchCodexOfficialAccount: vi.fn().mockResolvedValue({
        status: "not_logged_in",
        email: null,
        plan_type: null,
        auth_mode: null,
      }),
      startCodexOfficialLogin: vi.fn().mockResolvedValue({
        login_id: "login-1",
        verification_url: "https://auth.openai.com/codex/device",
        user_code: "ABCD-1234",
      }),
    });
    store.setState({ catalog: { ...catalog, connections: [official] } });
    store.getState().openConnection(official.id);

    await store.getState().refreshCodexOfficialAccount();
    const login = await store.getState().beginCodexOfficialLogin();

    expect(login?.user_code).toBe("ABCD-1234");
    expect(store.getState().connectionEditor?.officialAccount.account?.status).toBe(
      "not_logged_in",
    );
    expect(store.getState().connectionEditor?.officialAccount.login?.login_id).toBe(
      "login-1",
    );
  });

  it("Official 账号晚到响应不会覆盖后来打开的供应商", async () => {
    const firstAccount = deferred<{
      status: "logged_in";
      email: string;
      plan_type: string;
      auth_mode: string;
    }>();
    const first = {
      ...catalog.connections[0],
      id: "official-1",
      runtime: "codex" as const,
      kind: "codex_official" as const,
      protocol: "codex_official" as const,
      base_url: null,
      upstream_host: null,
      codex_catalog: [],
    };
    const second = { ...first, id: "official-2", name: "第二个账号" };
    const store = createSettingsStore({
      fetchCodexOfficialAccount: vi.fn().mockReturnValue(firstAccount.promise),
    });
    store.setState({ catalog: { ...catalog, connections: [first, second] } });
    store.getState().openConnection(first.id);

    const pending = store.getState().refreshCodexOfficialAccount();
    store.getState().openConnection(second.id);
    firstAccount.resolve({
      status: "logged_in",
      email: "first@example.com",
      plan_type: "pro",
      auth_mode: "chatgpt",
    });
    await pending;

    expect(store.getState().connectionEditor?.connectionId).toBe(second.id);
    expect(store.getState().connectionEditor?.officialAccount.account).toBeNull();
  });

  it("Official 更换账号时不会把旧账号误判为本次登录完成", async () => {
    const official = {
      ...catalog.connections[0],
      id: "official-1",
      runtime: "codex" as const,
      kind: "codex_official" as const,
      protocol: "codex_official" as const,
      base_url: null,
      upstream_host: null,
      codex_catalog: [],
    };
    const fetchAccount = vi
      .fn()
      .mockResolvedValueOnce({
        status: "logged_in",
        email: "old@example.com",
        plan_type: "pro",
        auth_mode: "chatgpt",
      })
      .mockResolvedValueOnce({
        status: "logged_in",
        email: "old@example.com",
        plan_type: "pro",
        auth_mode: "chatgpt",
      })
      .mockResolvedValueOnce({
        status: "logged_in",
        email: "new@example.com",
        plan_type: "pro",
        auth_mode: "chatgpt",
      });
    const store = createSettingsStore({
      fetchCodexOfficialAccount: fetchAccount,
      startCodexOfficialLogin: vi.fn().mockResolvedValue({
        login_id: "login-1",
        verification_url: "https://auth.openai.com/codex/device",
        user_code: "ABCD-1234",
      }),
    });
    store.setState({ catalog: { ...catalog, connections: [official] } });
    store.getState().openConnection(official.id);
    await store.getState().refreshCodexOfficialAccount();
    expect(store.getState().catalog?.connections[0]?.auth.status).toBe("referenced");

    await store.getState().beginCodexOfficialLogin();
    await store.getState().refreshCodexOfficialAccount();
    expect(store.getState().connectionEditor?.officialAccount.login).not.toBeNull();

    await store.getState().refreshCodexOfficialAccount();
    expect(store.getState().connectionEditor?.officialAccount.login).toBeNull();
    expect(store.getState().connectionEditor?.officialAccount.account?.email).toBe(
      "new@example.com",
    );
  });

  it("Official 原生完成通知可识别同一邮箱重新登录", async () => {
    const official = {
      ...catalog.connections[0],
      id: "official-1",
      runtime: "codex" as const,
      kind: "codex_official" as const,
      protocol: "codex_official" as const,
      base_url: null,
      upstream_host: null,
      codex_catalog: [],
    };
    const fetchAccount = vi
      .fn()
      .mockResolvedValueOnce({
        status: "logged_in",
        email: "same@example.com",
        plan_type: "pro",
        auth_mode: "chatgpt",
      })
      .mockResolvedValueOnce({
        status: "logged_in",
        email: "same@example.com",
        plan_type: "pro",
        auth_mode: "chatgpt",
        login_id: "login-1",
        login_status: "completed",
        login_error: null,
      });
    const startLogin = vi.fn().mockResolvedValue({
      login_id: "login-1",
      verification_url: "https://auth.openai.com/codex/device",
      user_code: "ABCD-1234",
    });
    const store = createSettingsStore({
      fetchCodexOfficialAccount: fetchAccount,
      startCodexOfficialLogin: startLogin,
    });
    store.setState({ catalog: { ...catalog, connections: [official] } });
    store.getState().openConnection(official.id);
    await store.getState().refreshCodexOfficialAccount();
    await store.getState().beginCodexOfficialLogin();

    await store.getState().refreshCodexOfficialAccount();

    expect(store.getState().connectionEditor?.officialAccount.login).toBeNull();
    expect(startLogin).toHaveBeenCalledOnce();
  });

  it("五项任务按行保存，失败不会清除该行脏状态", async () => {
    const putTaskBinding = vi
      .fn()
      .mockRejectedValueOnce(new Error("任务绑定保存失败"))
      .mockResolvedValueOnce({
        task_id: "memory_weekly",
        version: 1,
        session_configuration_id: "weekly-only",
      });
    const store = createSettingsStore({ putTaskBinding });
    store.setState({ catalog });
    store.getState().setTaskDraft("memory_weekly", "weekly-only");
    store.getState().setTaskEnabled("memory_weekly", true);

    await store.getState().saveTask("memory_weekly");
    expect(store.getState().taskDirty.memory_weekly).toBe(true);
    expect(store.getState().taskErrors.memory_weekly).toBe("任务绑定保存失败");

    await store.getState().saveTask("memory_weekly");
    expect(store.getState().taskDirty.memory_weekly).toBe(false);
    expect(putTaskBinding).toHaveBeenNthCalledWith(
      2,
      "memory_weekly",
      "weekly-only",
      0,
    );
  });

  it("任务保存返回后继续排空等待期间的停用修改", async () => {
    const pendingBinding = deferred<{
      task_id: "memory_weekly";
      version: number;
      session_configuration_id: string;
    }>();
    const deleteTaskBinding = vi.fn().mockResolvedValue({
      task_id: "memory_weekly",
      version: 2,
      session_configuration_id: null,
    });
    const store = createSettingsStore({
      putTaskBinding: vi.fn().mockReturnValue(pendingBinding.promise),
      deleteTaskBinding,
    });
    store.setState({ catalog });
    store.getState().setTaskDraft("memory_weekly", "weekly-only");
    store.getState().setTaskEnabled("memory_weekly", true);

    const pending = store.getState().saveTask("memory_weekly");
    store.getState().setTaskEnabled("memory_weekly", false);
    pendingBinding.resolve({
      task_id: "memory_weekly",
      version: 1,
      session_configuration_id: "weekly-only",
    });
    await pending;

    expect(store.getState().taskDrafts.memory_weekly).toBe("weekly-only");
    expect(deleteTaskBinding).toHaveBeenCalledWith("memory_weekly", 1);
    expect(store.getState().taskDirty.memory_weekly).toBe(false);
  });

  it("后台任务关闭开关时删除绑定并保留所选配置供再次开启", async () => {
    const boundCatalog: ConfigurationCatalog = {
      ...catalog,
      task_bindings: [{
        task_id: "memory_weekly",
        version: 2,
        session_configuration_id: "weekly-only",
      }],
    };
    const deleteTaskBinding = vi.fn().mockResolvedValue({
      task_id: "memory_weekly",
      version: 3,
      session_configuration_id: null,
    });
    const store = createSettingsStore({ deleteTaskBinding });
    store.setState({ catalog: boundCatalog });
    store.getState().reloadTask("memory_weekly");

    store.getState().setTaskEnabled("memory_weekly", false);
    await store.getState().saveTask("memory_weekly");

    expect(deleteTaskBinding).toHaveBeenCalledWith("memory_weekly", 2);
    expect(store.getState().taskDrafts.memory_weekly).toBe("weekly-only");
    expect(store.getState().taskEnabled.memory_weekly).toBe(false);
    expect(store.getState().taskDirty.memory_weekly).toBe(false);
  });

  it("自动保存会在首个任务请求结束后继续提交等待期间的新选择", async () => {
    const first = deferred<{
      task_id: "memory_weekly";
      version: number;
      session_configuration_id: string;
    }>();
    const putTaskBinding = vi
      .fn()
      .mockReturnValueOnce(first.promise)
      .mockResolvedValueOnce({
        task_id: "memory_weekly",
        version: 2,
        session_configuration_id: "weekly-new",
      });
    const store = createSettingsStore({ putTaskBinding });
    store.setState({ catalog });
    store.getState().setTaskDraft("memory_weekly", "weekly-only");
    store.getState().setTaskEnabled("memory_weekly", true);

    const pending = store.getState().saveTask("memory_weekly");
    store.getState().setTaskDraft("memory_weekly", "weekly-new");
    first.resolve({
      task_id: "memory_weekly",
      version: 1,
      session_configuration_id: "weekly-only",
    });
    await pending;

    expect(putTaskBinding).toHaveBeenNthCalledWith(
      2,
      "memory_weekly",
      "weekly-new",
      1,
    );
    expect(store.getState().taskDirty.memory_weekly).toBe(false);
  });

  it("Agent 默认发生版本冲突时保留本地草稿", async () => {
    const store = createSettingsStore({
      putAgentDefaults: vi.fn().mockRejectedValue(
        new ConfigurationApiError(
          "VERSION_CONFLICT",
          "配置已被其他窗口更新",
          409,
        ),
      ),
    });
    store.setState({ catalog, agentDraft: catalog.agent_defaults });
    store.getState().updateAgentDraft({ memory_enabled: false });

    await store.getState().saveAgentDefaults();

    expect(store.getState().agentDraft.memory_enabled).toBe(false);
    expect(store.getState().agentDirty).toBe(true);
    expect(store.getState().agentConflict).toBe(true);
  });

  it("Agent 默认自动保存会继续提交等待期间的新修改", async () => {
    const pendingDefaults = deferred<AgentDefaults>();
    const putAgentDefaults = vi
      .fn()
      .mockReturnValueOnce(pendingDefaults.promise)
      .mockImplementationOnce(async (defaults: AgentDefaults) => ({
        ...defaults,
        version: 2,
      }));
    const store = createSettingsStore({
      putAgentDefaults,
    });
    store.setState({ catalog, agentDraft: catalog.agent_defaults });
    store.getState().updateAgentDraft({ memory_enabled: false });

    const pending = store.getState().saveAgentDefaults();
    store.getState().updateAgentDraft({ profile_enabled: false });
    pendingDefaults.resolve({
      ...catalog.agent_defaults,
      version: 1,
      memory_enabled: false,
    });
    await pending;

    expect(store.getState().agentDraft.version).toBe(2);
    expect(store.getState().agentDraft.profile_enabled).toBe(false);
    expect(store.getState().agentDirty).toBe(false);
    expect(putAgentDefaults).toHaveBeenCalledTimes(2);
  });
});
