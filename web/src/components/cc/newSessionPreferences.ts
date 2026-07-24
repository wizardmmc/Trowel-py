import type { NewSessionConfig } from "./NewSessionDialog";

const STORAGE_KEY = "trowel.new-session-preferences";
const STORAGE_VERSION = 1;
const CODEX_PERMISSIONS = new Set([
  "follow",
  "read-only",
  "workspace-write",
  "danger-full-access",
]);

export function loadNewSessionPreferences(): NewSessionConfig | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    if (!isRecord(parsed) || parsed.version !== STORAGE_VERSION) return null;
    return parseConfig(parsed.config);
  } catch {
    return null;
  }
}

export function saveNewSessionPreferences(config: NewSessionConfig): void {
  try {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ version: STORAGE_VERSION, config }),
    );
  } catch {
    // 存储不可用不能反向影响已经成功的新建请求。
  }
}

function parseConfig(value: unknown): NewSessionConfig | null {
  if (!isRecord(value)) return null;
  if (value.runtime !== "claude_code" && value.runtime !== "codex") return null;
  if (
    typeof value.model !== "string" ||
    typeof value.effort !== "string" ||
    typeof value.permission_mode !== "string" ||
    typeof value.memory_enabled !== "boolean" ||
    typeof value.profile_enabled !== "boolean"
  ) {
    return null;
  }
  const preset = value.permission_preset;
  if (
    preset !== undefined &&
    (typeof preset !== "string" || !CODEX_PERMISSIONS.has(preset))
  ) {
    return null;
  }
  return {
    runtime: value.runtime,
    model: value.model,
    effort: value.effort,
    permission_mode: value.permission_mode,
    permission_preset: preset as NewSessionConfig["permission_preset"],
    memory_enabled: value.memory_enabled,
    profile_enabled: value.profile_enabled,
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
