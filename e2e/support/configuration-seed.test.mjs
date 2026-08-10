/** 验证配置种子失败只留下低基数阶段和错误类别。 */

import { describe, expect, test } from "bun:test";
import { seedRuntimeConfigurations } from "./configuration-seed.mjs";

describe("runtime configuration seed diagnostics", () => {
  test("records the failing runtime and stage without copying an error message", async () => {
    const steps = [];
    const api = {
      async post() {
        return { id: "connection", version: 1 };
      },
      async put() {
        const error = new Error("must not enter the trace");
        error.name = "DesktopApiError";
        error.status = 409;
        error.code = "VERSION_CONFLICT";
        throw error;
      },
    };

    await expect(
      seedRuntimeConfigurations(api, "http://127.0.0.1:1", {
        record(name, details = {}) {
          steps.push({ name, details });
        },
      }),
    ).rejects.toThrow("must not enter the trace");

    expect(steps.at(-1)).toEqual({
      name: "configuration.seed_failed",
      details: {
        runtime: "claude_code",
        stage: "save_secret",
        status_bucket: "4xx",
        error_code: "VERSION_CONFLICT",
        error_type: "DesktopApiError",
      },
    });
    expect(JSON.stringify(steps)).not.toContain("must not enter the trace");
  });
});
