/** 从连接草稿生成只读、脱敏且符合 runtime 语义的配置预览。 */

import type {
  ConnectionEditorState,
  ConnectionKind,
} from "../domain/types";

export interface ConnectionPreviewDocument {
  /** 页面标注的序列化格式。 */
  readonly format: "JSON" | "TOML";
  /** 已序列化且不包含凭据原文的预览正文。 */
  readonly text: string;
}

const CLAUDE_ROLE_ENV: Readonly<Record<string, string>> = {
  default: "ANTHROPIC_MODEL",
  sonnet: "ANTHROPIC_DEFAULT_SONNET_MODEL",
  opus: "ANTHROPIC_DEFAULT_OPUS_MODEL",
  fable: "ANTHROPIC_DEFAULT_FABLE_MODEL",
  haiku: "ANTHROPIC_DEFAULT_HAIKU_MODEL",
  subagent: "CLAUDE_CODE_SUBAGENT_MODEL",
};

/** 选择对应 runtime 的真实配置形态，不把 Codex TOML 显示成 JSON。 */
export function buildConnectionPreview(
  editor: ConnectionEditorState,
  authStatus: string,
  claudeConfigInherited = false,
  codexConfigInherited = false,
): ConnectionPreviewDocument {
  const builders: Readonly<
    Record<ConnectionKind, () => ConnectionPreviewDocument>
  > = {
    claude_compatible: () =>
      claudePreview(editor, authStatus, claudeConfigInherited),
    codex_official: () => codexOfficialPreview(editor, codexConfigInherited),
    codex_custom: () => codexCustomPreview(editor, authStatus, codexConfigInherited),
    direct_api: () => directApiPreview(editor, authStatus),
  };
  return builders[editor.draft.kind]();
}

/** 生成 Claude Code 使用的脱敏环境变量 JSON。 */
function claudePreview(
  editor: ConnectionEditorState,
  authStatus: string,
  inherited: boolean,
): ConnectionPreviewDocument {
  const env: Record<string, string | null> = {
    ANTHROPIC_BASE_URL: safeUrl(editor.draft.base_url),
    ANTHROPIC_AUTH_TOKEN: `<${authStatus}>`,
  };
  for (const [role, model] of Object.entries(editor.draft.claude_role_models)) {
    const envName = CLAUDE_ROLE_ENV[role];
    if (envName) env[envName] = model;
  }
  const document: Record<string, unknown> = {
    env,
    trowel_connection_home: {
      inherited,
      provider_settings: "per-session override",
    },
  };
  if (editor.draft.claude_auto_memory_disabled) {
    document.autoMemoryEnabled = false;
  }
  return {
    format: "JSON",
    text: JSON.stringify(document, null, 2),
  };
}

/** 生成不暴露账号槽路径的 Codex Official TOML 摘要。 */
function codexOfficialPreview(
  editor: ConnectionEditorState,
  inherited: boolean,
): ConnectionPreviewDocument {
  const draft = editor.draft;
  const lines = [
    'model_provider = "openai"',
    'oauth = "<managed by Codex>"',
    `trowel_config_copy = ${tomlString(inherited ? "copied" : "not-copied")}`,
  ];
  if (draft.proxy_url) lines.push(`proxy_url = ${tomlString(safeUrl(draft.proxy_url))}`);
  if (draft.proxy_username) {
    lines.push(`proxy_username = ${tomlString(draft.proxy_username)}`);
  }
  return { format: "TOML", text: lines.join("\n") };
}

/** 生成 Codex Responses provider 及所选 catalog 的 TOML 摘要。 */
function codexCustomPreview(
  editor: ConnectionEditorState,
  authStatus: string,
  inherited: boolean,
): ConnectionPreviewDocument {
  const draft = editor.draft;
  const providerId = editor.connectionId ?? "<saved connection id>";
  const selectedModels = draft.codex_catalog.map((item) => item.id);
  const lines = [
    `model_provider = ${tomlString(providerId)}`,
    ...(selectedModels[0] ? [`model = ${tomlString(selectedModels[0])}`] : []),
    "",
    `[model_providers.${tomlString(providerId)}]`,
    `base_url = ${tomlString(safeUrl(draft.base_url))}`,
    'wire_api = "responses"',
    `api_key = ${tomlString(`<${authStatus}>`)}`,
    "requires_openai_auth = false",
    "",
    "[trowel]",
    `config_copy = ${tomlString(inherited ? "copied" : "not-copied")}`,
    `catalog_models = ${tomlStringArray(selectedModels)}`,
  ];
  return { format: "TOML", text: lines.join("\n") };
}

/** 生成仅供后台任务使用的 direct API 脱敏请求摘要。 */
function directApiPreview(
  editor: ConnectionEditorState,
  authStatus: string,
): ConnectionPreviewDocument {
  return {
    format: "JSON",
    text: JSON.stringify(
      {
        protocol: editor.draft.protocol,
        base_url: safeUrl(editor.draft.base_url),
        models_url: safeUrl(editor.draft.models_url),
        api_key: `<${authStatus}>`,
      },
      null,
      2,
    ),
  };
}

/** 把可空字符串编码成 TOML 基本字符串。 */
function tomlString(value: string | null): string {
  return JSON.stringify(value ?? "<unset>");
}

/** 把字符串列表编码成紧凑 TOML 数组。 */
function tomlStringArray(values: readonly string[]): string {
  return `[${values.map((value) => tomlString(value)).join(", ")}]`;
}

/** 预览不重复展示 URL 中可能误填的用户名或密码。 */
function safeUrl(value: string | null): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    url.username = "";
    url.password = "";
    return url.toString();
  } catch {
    return value;
  }
}
