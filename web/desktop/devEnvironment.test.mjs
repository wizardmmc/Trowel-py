/** 验证开发 Host 不会继承启动它的另一个 Trowel Desktop 实例。 */

// @vitest-environment node

import { expect, it } from "vitest";
import {
  buildDevelopmentDesktopEnvironment,
  resolveDevelopmentDataMode,
} from "./devEnvironment.mjs";

it("uses long-term App data unless isolated mode is explicit", () => {
  expect(resolveDevelopmentDataMode(["node", "dev.mjs"])).toBe("canonical-dev");
  expect(resolveDevelopmentDataMode(["node", "dev.mjs", "--isolated"])).toBe(
    "isolated-dev",
  );
  expect(resolveDevelopmentDataMode(["node", "dev.mjs", "--observe"])).toBe(
    "isolated-dev",
  );
  expect(
    resolveDevelopmentDataMode(["node", "dev.mjs", "--smoke"], {
      usesTemporaryDataRoot: true,
    }),
  ).toBe("isolated-dev");
});

it("removes parent Desktop Host ownership while preserving runtime settings", () => {
  const environment = buildDevelopmentDesktopEnvironment(
    {
      HOME: "/Users/developer",
      PATH: "/runtime/bin:/usr/bin",
      CODEX_HOME: "/Users/developer/.codex",
      TROWEL_PYTHON_EXECUTABLE: "/repo/.venv/bin/python",
      TROWEL_APP_INSTANCE_ID: "stable-instance",
      TROWEL_DATA_ROOT: "/stable/data",
      TROWEL_DESKTOP_CREDENTIAL: "stable-secret",
      TROWEL_DESKTOP_DATA_DIR: "/stable/data",
      TROWEL_DESKTOP_DATA_MODE: "packaged",
      TROWEL_DESKTOP_INSPECTION_ONLY: "1",
      TROWEL_DESKTOP_LOG_DIR: "/stable/logs",
      TROWEL_DESKTOP_READ_DATA_DIR: "/stable/read-data",
      TROWEL_DESKTOP_RENDERER_ORIGIN: "file://stable",
      TROWEL_DESKTOP_SERVICE_FILE: "/stable/service.json",
      TROWEL_DESKTOP_SMOKE: "1",
      TROWEL_ELECTRON_USER_DATA_DIR: "/stable/electron",
      TROWEL_PROJECT_ROOT: "/stable/project",
      TROWEL_RENDERER_URL: "http://stable.invalid",
      TROWEL_SERVER_PORT: "43123",
    },
    {
      TROWEL_PROJECT_ROOT: "/dev/project",
      TROWEL_RENDERER_URL: "http://127.0.0.1:4173",
      TROWEL_DESKTOP_SERVICE_FILE: "/tmp/dev-service.json",
      TROWEL_DESKTOP_DATA_MODE: "canonical-dev",
    },
  );

  expect(environment).toMatchObject({
    HOME: "/Users/developer",
    PATH: "/runtime/bin:/usr/bin",
    CODEX_HOME: "/Users/developer/.codex",
    TROWEL_PYTHON_EXECUTABLE: "/repo/.venv/bin/python",
    TROWEL_DESKTOP_DATA_MODE: "canonical-dev",
    TROWEL_PROJECT_ROOT: "/dev/project",
    TROWEL_RENDERER_URL: "http://127.0.0.1:4173",
    TROWEL_DESKTOP_SERVICE_FILE: "/tmp/dev-service.json",
  });
  for (const name of [
    "TROWEL_APP_INSTANCE_ID",
    "TROWEL_DATA_ROOT",
    "TROWEL_DESKTOP_CREDENTIAL",
    "TROWEL_DESKTOP_DATA_DIR",
    "TROWEL_DESKTOP_INSPECTION_ONLY",
    "TROWEL_DESKTOP_LOG_DIR",
    "TROWEL_DESKTOP_READ_DATA_DIR",
    "TROWEL_DESKTOP_RENDERER_ORIGIN",
    "TROWEL_DESKTOP_SMOKE",
    "TROWEL_ELECTRON_USER_DATA_DIR",
    "TROWEL_SERVER_PORT",
  ]) {
    expect(environment[name]).toBeUndefined();
  }
});
