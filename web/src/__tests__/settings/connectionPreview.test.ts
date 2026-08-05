/** 验证连接预览遵循 runtime 格式并移除 URL 内嵌凭据。 */

import { expect, it } from "vitest";
import type { ConnectionEditorState } from "../../settings/domain/types";
import { buildConnectionPreview } from "../../settings/ui/connectionPreview";

it("renders Codex custom as redacted TOML instead of JSON", () => {
  const editor: ConnectionEditorState = {
    connectionId: "deepseek-a",
    version: 3,
    draft: {
      name: "DeepSeek A",
      runtime: "codex",
      kind: "codex_custom",
      protocol: "openai_responses",
      base_url: "https://user:password@api.deepseek.com/v1",
      models_url: null,
      login_directory: null,
      proxy_url: null,
      proxy_username: null,
      claude_role_models: {},
      codex_catalog: [
        {
          id: "deepseek-v4-flash",
          display_name: null,
          default_effort: "high",
          supported_efforts: ["low", "high"],
        },
      ],
      catalog_request_identity: "request-1",
    },
    dirty: true,
    saving: false,
    deleting: false,
    error: null,
    conflict: false,
    modelFetch: {
      status: "ready",
      models: ["deepseek-v4-flash"],
      sourceEndpoint: "https://api.deepseek.com/v1/models",
      fetchedAt: "2026-08-04T12:00:00Z",
      requestIdentity: "request-1",
      error: null,
    },
  };

  const preview = buildConnectionPreview(editor, "configured");

  expect(preview.format).toBe("TOML");
  expect(preview.text).toContain('wire_api = "responses"');
  expect(preview.text).toContain('catalog_models = ["deepseek-v4-flash"]');
  expect(preview.text).not.toContain("password");
});
