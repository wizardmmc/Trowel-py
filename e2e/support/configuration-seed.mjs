/** 只通过生产配置 API 创建行为 E2E 所需的 Claude 与 Codex 运行配置。 */

/** 创建两种已取回 catalog、已选择模型的连接和命名会话配置。 */
export async function seedRuntimeConfigurations(api, modelCatalogBaseUrl, trace = null) {
  trace?.record("configuration.seed_started");
  const claude = await seedConnection(api, {
    name: "E2E Claude",
    runtime: "claude_code",
    kind: "claude_compatible",
    protocol: "anthropic_messages",
    base_url: modelCatalogBaseUrl,
    selectedModel: "e2e-claude-model",
  }, trace);
  const codex = await seedConnection(api, {
    name: "E2E Codex",
    runtime: "codex",
    kind: "codex_custom",
    protocol: "openai_responses",
    base_url: modelCatalogBaseUrl,
    selectedModel: "gpt-5.6-sol",
  }, trace);
  trace?.record("configuration.seed_completed");
  return { claude, codex };
}

/** 走 create→secret→fetch→save→session configuration 完整生产链。 */
async function seedConnection(api, draft, trace) {
  const editable = {
    name: draft.name,
    runtime: draft.runtime,
    kind: draft.kind,
    protocol: draft.protocol,
    base_url: draft.base_url,
  };
  let stage = "create_connection";
  try {
    let connection = await api.post("/api/configuration/connections", editable);
    trace?.record("configuration.connection_created", { runtime: draft.runtime });
    stage = "save_secret";
    const secret = await api.put(
      `/api/configuration/connections/${connection.id}/secrets/api_key`,
      { expected_version: connection.version, action: "set", value: "e2e-only-key" },
    );
    trace?.record("configuration.secret_saved", { runtime: draft.runtime });
    stage = "fetch_catalog";
    const fetched = await api.post(
      `/api/configuration/connections/${connection.id}/models:fetch`,
      { expected_version: secret.version },
    );
    trace?.record("configuration.catalog_fetched", { runtime: draft.runtime });
    const update = {
      ...editable,
      expected_version: fetched.connection_version,
      catalog_request_identity: fetched.request_identity,
    };
    if (draft.runtime === "claude_code") {
      update.claude_role_models = {
        default: draft.selectedModel,
        sonnet: draft.selectedModel,
        opus: draft.selectedModel,
        haiku: draft.selectedModel,
        subagent: draft.selectedModel,
      };
    } else {
      const entry = fetched.codex_catalog.find(
        (item) => item.id === draft.selectedModel,
      );
      if (!entry) {
        throw new Error("recorded Codex model was not returned by the production catalog");
      }
      update.codex_catalog = [entry];
    }
    stage = "save_connection";
    connection = await api.put(
      `/api/configuration/connections/${connection.id}`,
      update,
    );
    trace?.record("configuration.connection_saved", { runtime: draft.runtime });
    stage = "create_session_configuration";
    const configuration = await api.post(
      "/api/configuration/session-configurations",
      {
        name: draft.name,
        connection_id: connection.id,
        model: draft.selectedModel,
        effort: draft.runtime === "codex" ? "high" : null,
        expected_connection_version: connection.version,
      },
    );
    trace?.record("configuration.session_created", { runtime: draft.runtime });
    return { connection, configuration };
  } catch (error) {
    trace?.record("configuration.seed_failed", {
      runtime: draft.runtime,
      stage,
      status_bucket: httpStatusBucket(error?.status),
      error_code: stableErrorCode(error?.code),
      error_type: stableErrorType(error?.name),
    });
    throw error;
  }
}

/** 把 HTTP 状态限制为低基数类别，未取得响应时返回 none。 */
function httpStatusBucket(status) {
  if (!Number.isInteger(status)) return "none";
  if (status >= 200 && status < 300) return "2xx";
  if (status >= 400 && status < 500) return "4xx";
  if (status >= 500 && status < 600) return "5xx";
  return "other";
}

/** 只允许配置域的稳定大写错误码进入脱敏 trace。 */
function stableErrorCode(code) {
  return typeof code === "string" && /^[A-Z][A-Z0-9_]{0,63}$/.test(code)
    ? code
    : "none";
}

/** 把运行时异常类型压成固定集合，不复制上游错误正文。 */
function stableErrorType(name) {
  return ["DesktopApiError", "TimeoutError", "AbortError"].includes(name)
    ? name
    : "other";
}
