/** 隔离开发 Electron Host 与启动它的父 Desktop Host 环境。 */

const DESKTOP_HOST_OWNED_VARIABLES = [
  "TROWEL_APP_INSTANCE_ID",
  "TROWEL_DATA_ROOT",
  "TROWEL_DESKTOP_CREDENTIAL",
  "TROWEL_DESKTOP_DATA_DIR",
  "TROWEL_DESKTOP_DATA_MODE",
  "TROWEL_DESKTOP_DIAGNOSTIC_SMOKE",
  "TROWEL_DESKTOP_LOG_DIR",
  "TROWEL_DESKTOP_RENDERER_ORIGIN",
  "TROWEL_DESKTOP_RENDERER_CRASH_SMOKE",
  "TROWEL_DESKTOP_RESIDENCY_SMOKE",
  "TROWEL_DESKTOP_SERVICE_FILE",
  "TROWEL_DESKTOP_SINGLE_INSTANCE_SMOKE",
  "TROWEL_DESKTOP_SMOKE",
  "TROWEL_ELECTRON_USER_DATA_DIR",
  "TROWEL_PROJECT_ROOT",
  "TROWEL_RENDERER_URL",
  "TROWEL_SERVER_PORT",
];

/** 清除父 Host 的实例所有权，再应用本次开发启动明确生成的设置。 */
export function buildDevelopmentDesktopEnvironment(parent, overrides) {
  const environment = { ...parent };
  for (const name of DESKTOP_HOST_OWNED_VARIABLES) delete environment[name];
  return { ...environment, ...overrides };
}

/** 日常开发默认使用长期数据，只有显式参数才切换到隔离沙箱。 */
export function resolveDevelopmentDataMode(argv) {
  return argv.includes("--isolated") ? "isolated-dev" : "canonical-dev";
}
