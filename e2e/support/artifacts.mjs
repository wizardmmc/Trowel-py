/** 生成可读但不携带用户正文、凭据、额度和本机路径的 E2E 产物。 */

import { createHash } from "node:crypto";
import { mkdir, readFile, readdir, rm, writeFile } from "node:fs/promises";
import path from "node:path";

const SECRET_KEYS = /(?:api[_-]?key|authorization|credential|secret|token|usage|quota)/i;
const PATH_KEYS = /(?:cwd|directory|file|home|path|workdir|workspace)/i;
const CONTENT_KEYS = /(?:answer|body|content|instruction|message|prompt|question|text|topic)/i;
const ABSOLUTE_PATH = /(?:^|[\s"'(=])(?:\/Users\/|\/private\/|\/var\/folders\/|\/tmp\/)[^\s"')]+/m;
const CREDENTIAL = /(?:Bearer\s+|api[_-]?key\s*[=:]\s*|credential\s*[=:]\s*)[^\s,"']+/i;
const FAILURE_CATEGORIES = [
  ["locator.click", "locator_click_timeout"],
  ["locator.fill", "locator_fill_timeout"],
  ["locator.waitFor", "locator_wait_timeout"],
  ["page.waitForResponse", "response_wait_timeout"],
  ["desktop sidecar descriptor", "sidecar_readiness"],
  ["resource", "resource_assertion"],
  ["expect.poll", "poll_assertion"],
  ["Timeout", "timeout"],
];

/** 表示产物审计发现了不能离开隔离测试目录的内容。 */
export class ArtifactPrivacyError extends Error {
  /**
   * @param {string[]} reasons 命中的稳定风险类别，不包含泄漏原文。
   */
  constructor(reasons) {
    super(`E2E artifact privacy audit failed: ${reasons.join(", ")}`);
    this.name = "ArtifactPrivacyError";
    this.reasons = [...reasons];
  }
}

/** 递归保留业务状态和稳定 ID，同时替换敏感字段的值。 */
export function redactArtifactValue(value, key = "") {
  if (SECRET_KEYS.test(key)) return "[REDACTED]";
  if (PATH_KEYS.test(key)) return "[PATH]";
  if (CONTENT_KEYS.test(key)) return "[CONTENT]";
  if (Array.isArray(value)) {
    return value.map((item) => redactArtifactValue(item));
  }
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value).map(([childKey, childValue]) => [
        childKey,
        redactArtifactValue(childValue, childKey),
      ]),
    );
  }
  return value;
}

/**
 * 扫描准备保留的文本，命中时只报告类别，避免错误消息再次复制秘密。
 *
 * @param {string} text 待保存或上传的产物正文。
 * @param {{forbiddenContents?: string[]}} options 场景已知的测试正文哨兵。
 */
export function auditArtifactText(text, { forbiddenContents = [] } = {}) {
  const reasons = [];
  if (ABSOLUTE_PATH.test(text)) reasons.push("absolute_path");
  if (CREDENTIAL.test(text)) reasons.push("credential");
  if (forbiddenContents.some((value) => value && text.includes(value))) {
    reasons.push("scenario_content");
  }
  if (reasons.length > 0) throw new ArtifactPrivacyError(reasons);
}

/** 把原始异常压成不携带路径、正文或凭据的稳定诊断类别。 */
export function classifyFailureErrors(errors) {
  const classified = errors.map((error) => {
    const message = String(error?.message ?? "");
    return FAILURE_CATEGORIES.find(([needle]) => message.includes(needle))?.[1]
      ?? "assertion";
  });
  return [...new Set(classified.length > 0 ? classified : ["assertion"])];
}

/** 保存无时间抖动的步骤摘要，供成功产物和失败诊断共同使用。 */
export class StructuredTrace {
  /** @param {string} scenarioId 风险矩阵对应的稳定场景 ID。 */
  constructor(scenarioId) {
    this.scenarioId = scenarioId;
    this.steps = [];
  }

  /** @param {string} name 稳定步骤名。 @param {object} details 去敏前的结构化事实。 */
  record(name, details = {}) {
    this.steps.push({ name, details: redactArtifactValue(details) });
  }

  /** 返回可以直接序列化的版本化 trace。 */
  toJSON() {
    return {
      schema: "trowel-e2e-trace-v1",
      scenario_id: this.scenarioId,
      steps: [...this.steps],
    };
  }

  /** 写入并复读审计摘要，任何审计失败都会删除目标文件。 */
  async write(filePath, options = {}) {
    await mkdir(path.dirname(filePath), { recursive: true });
    const body = `${JSON.stringify(this.toJSON(), null, 2)}\n`;
    try {
      auditArtifactText(body, options);
      await writeFile(filePath, body, "utf8");
      auditArtifactText(await readFile(filePath, "utf8"), options);
    } catch (error) {
      await rm(filePath, { force: true });
      throw error;
    }
  }
}

/** 收集不含消息正文、URL 参数和 DOM 内容的失败诊断计数。 */
export class FailureDiagnostics {
  constructor() {
    this.consoleCounts = {};
    this.responseCounts = {};
    this.routeCounts = {};
    this.requestFailureCounts = {};
    this.listeners = null;
  }

  /** 从 renderer 开始记录 console 类型、响应状态桶和失败请求所属领域。 */
  attach(page) {
    if (this.listeners) throw new Error("failure diagnostics already attached");
    const onConsole = (message) => this.recordConsole(message.type());
    const onResponse = (response) =>
      this.recordResponse(response.status(), response.url());
    const onRequestFailed = (request) => this.recordRequestFailure(request.url());
    page.on("console", onConsole);
    page.on("response", onResponse);
    page.on("requestfailed", onRequestFailed);
    this.listeners = { page, onConsole, onResponse, onRequestFailed };
  }

  /** 停止监听，避免 teardown 自己产生的网络事件污染失败现场。 */
  detach() {
    if (!this.listeners) return;
    const { page, onConsole, onResponse, onRequestFailed } = this.listeners;
    page.off("console", onConsole);
    page.off("response", onResponse);
    page.off("requestfailed", onRequestFailed);
    this.listeners = null;
  }

  /** 只按 console 等级计数，不读取或保存消息正文。 */
  recordConsole(type) {
    increment(this.consoleCounts, allowValue(type, CONSOLE_TYPES));
  }

  /** 只记录 HTTP 状态桶和稳定 API 领域，不保存 URL。 */
  recordResponse(status, url) {
    increment(this.responseCounts, statusBucket(status));
    increment(this.routeCounts, routeGroup(url));
  }

  /** 只记录失败请求所属稳定领域，不保存失败原因或 URL。 */
  recordRequestFailure(url) {
    increment(this.requestFailureCounts, routeGroup(url));
  }

  /** 在 teardown 前冻结失败现场，避免进程退出和临时目录回收抹掉业务状态。 */
  async capture({ page = null, api = null, environment = null } = {}) {
    this.detach();
    const [dom, sessions, resources, desktopTransport] = await Promise.all([
      captureDomBusinessState(page),
      captureSessionBusinessState(api),
      captureResourceBusinessState(environment),
      captureDesktopTransportState(api, environment),
    ]);
    return {
      schema: "trowel-e2e-failure-v1",
      console_counts: { ...this.consoleCounts },
      network: {
        response_status_counts: { ...this.responseCounts },
        route_counts: { ...this.routeCounts },
        request_failure_counts: { ...this.requestFailureCounts },
      },
      business_state: {
        dom,
        sessions,
        resources,
        desktop_transport: desktopTransport,
      },
    };
  }

  /** 写出已冻结或现场采集的结构化白名单摘要，并复读执行同一隐私审计。 */
  async write(
    filePath,
    {
      page = null,
      api = null,
      environment = null,
      snapshot = null,
      failureCategories = null,
    } = {},
  ) {
    const captured = snapshot ?? await this.capture({ page, api, environment });
    const payload = failureCategories === null
      ? captured
      : { ...captured, failure_categories: [...failureCategories] };
    await mkdir(path.dirname(filePath), { recursive: true });
    const body = `${JSON.stringify(payload, null, 2)}\n`;
    try {
      auditArtifactText(body);
      await writeFile(filePath, body, "utf8");
      auditArtifactText(await readFile(filePath, "utf8"));
    } catch (error) {
      await rm(filePath, { force: true });
      throw error;
    }
  }
}

const CONSOLE_TYPES = new Set(["debug", "error", "info", "log", "warning"]);
const TURN_STATES = new Set([
  "idle",
  "starting",
  "running",
  "awaiting_input",
  "completed",
  "interrupted",
  "failed",
  "unknown",
]);
const SESSION_RESOURCE_STATES = new Set([
  "connected",
  "closing",
  "needs_reconcile",
  "closed",
]);
const LEDGER_RESOURCE_STATES = new Set([
  "running",
  "closing",
  "closed",
  "needs_reconcile",
]);
const RESOURCE_RUNTIMES = new Set(["app", "claude_code", "codex"]);
const RESOURCE_OWNER_SCOPES = new Set([
  "app",
  "runtime_connection",
  "session",
  "turn",
]);
const RESOURCE_KINDS = new Set([
  "sidecar_process_group",
  "claude_code_process_group",
  "session_title_process_group",
  "codex_app_server_connection",
  "codex_app_server_process_group",
  "codex_descendant_process_group",
  "codex_memory_mcp_process_group",
  "codex_agent_mcp_process_group",
  "codex_thread_resources",
  "codex_turn",
]);

/** 对白名单计数器增加一次。 */
function increment(counter, key) {
  counter[key] = (counter[key] ?? 0) + 1;
}

/** 未知枚举统一压成 other，防止上游字符串进入产物。 */
function allowValue(value, allowed) {
  return typeof value === "string" && allowed.has(value) ? value : "other";
}

/** 把 HTTP 状态压成低基数类别。 */
function statusBucket(status) {
  if (!Number.isInteger(status)) return "other";
  if (status >= 200 && status < 300) return "2xx";
  if (status >= 300 && status < 400) return "3xx";
  if (status >= 400 && status < 500) return "4xx";
  if (status >= 500 && status < 600) return "5xx";
  return "other";
}

/** 把 URL 只映射到预定义产品领域，不保留路径、查询或身份参数。 */
function routeGroup(value) {
  try {
    const pathname = new URL(value).pathname;
    if (!pathname.startsWith("/api/")) return "renderer";
    if (pathname === "/api/agent" || pathname.startsWith("/api/agent/")) {
      return "agent";
    }
    if (pathname.startsWith("/api/discussions")) return "discussion";
    if (pathname.startsWith("/api/desktop/")) return "desktop";
    if (pathname.startsWith("/api/config")) return "configuration";
    return "other_api";
  } catch {
    return "other";
  }
}

/** 从 DOM 只提取数量和白名单状态，不读取 textContent、value 或 HTML。 */
async function captureDomBusinessState(page) {
  if (!page) return { available: false };
  try {
    return await page.evaluate(() => {
      const turnStatusCounts = {};
      for (const element of document.querySelectorAll("[data-turn-status]")) {
        const value = element.getAttribute("data-turn-status") ?? "other";
        const key = ["active", "done", "error", "interrupted"].includes(value)
          ? value
          : "other";
        turnStatusCounts[key] = (turnStatusCounts[key] ?? 0) + 1;
      }
      return {
        available: true,
        turn_status_counts: turnStatusCounts,
        dialog_count: document.querySelectorAll('[role="dialog"]').length,
        alert_count: document.querySelectorAll('[role="alert"]').length,
        disabled_textbox_count: document.querySelectorAll(
          'textarea:disabled, input[type="text"]:disabled',
        ).length,
      };
    });
  } catch {
    return { available: false };
  }
}

/** 从活动会话接口只汇总生命周期枚举和数量。 */
async function captureSessionBusinessState(api) {
  if (!api) return { available: false };
  try {
    const active = await api.get("/api/agent/sessions/active");
    const turnStateCounts = {};
    const resourceStateCounts = {};
    for (const session of active.sessions ?? []) {
      increment(
        turnStateCounts,
        allowValue(session.turn_state ?? "idle", TURN_STATES),
      );
      increment(
        resourceStateCounts,
        allowValue(
          session.resource_state ?? "connected",
          SESSION_RESOURCE_STATES,
        ),
      );
    }
    return {
      available: true,
      session_count: active.sessions?.length ?? 0,
      turn_state_counts: turnStateCounts,
      resource_state_counts: resourceStateCounts,
    };
  } catch {
    return { available: false };
  }
}

/** 从资源快照只汇总 state/runtime/kind 数量，不保存 owner 或进程身份。 */
async function captureResourceBusinessState(environment) {
  if (!environment) return { available: false };
  try {
    const snapshot = await environment.readResourceSnapshot();
    const stateCounts = {};
    const runtimeCounts = {};
    const ownerScopeCounts = {};
    const resourceKindCounts = {};
    for (const resource of snapshot.resources ?? []) {
      increment(
        stateCounts,
        allowValue(resource.state, LEDGER_RESOURCE_STATES),
      );
      increment(
        runtimeCounts,
        allowValue(resource.runtime ?? "app", RESOURCE_RUNTIMES),
      );
      increment(
        ownerScopeCounts,
        allowValue(resource.owner_scope, RESOURCE_OWNER_SCOPES),
      );
      increment(
        resourceKindCounts,
        allowValue(resource.resource_kind, RESOURCE_KINDS),
      );
    }
    return {
      available: true,
      resource_count: snapshot.resources?.length ?? 0,
      state_counts: stateCounts,
      runtime_counts: runtimeCounts,
      owner_scope_counts: ownerScopeCounts,
      resource_kind_counts: resourceKindCounts,
    };
  } catch {
    return { available: false };
  }
}

/** 对比当前 descriptor 与测试客户端，不保存端口、凭据或实例身份。 */
async function captureDesktopTransportState(api, environment) {
  if (!api || !environment?.serviceDescriptorPath) return { available: false };
  try {
    const descriptor = JSON.parse(
      await readFile(environment.serviceDescriptorPath, "utf8"),
    );
    const baseUrl = descriptor.base_url ?? descriptor.baseUrl;
    const credential = descriptor.credential;
    const serviceInstanceId =
      descriptor.service_instance_id ?? descriptor.serviceInstanceId;
    const instanceIdentity = typeof serviceInstanceId === "string"
      ? createHash("sha256")
        .update(serviceInstanceId, "utf8")
        .digest("hex")
        .slice(0, 20)
      : null;
    let endpointMatches = false;
    try {
      endpointMatches = new URL(baseUrl).origin === api.baseUrl;
    } catch {
      endpointMatches = false;
    }
    return {
      available: true,
      endpoint_matches: endpointMatches,
      credential_matches: credential === api.credential,
      instance_matches: instanceIdentity === api.appInstanceIdentity,
    };
  } catch {
    return { available: false };
  }
}

/** 删除 Playwright 自动生成且未经允许的 DOM、截图或 trace 产物。 */
export async function removeUnexpectedPlaywrightArtifacts(
  outputDirectory,
  { allowedFiles = ["summary.json"] } = {},
) {
  const allowed = new Set(allowedFiles);
  let entries;
  try {
    entries = await readdir(outputDirectory, { withFileTypes: true });
  } catch (error) {
    if (error?.code === "ENOENT") return;
    throw error;
  }
  const unexpected = entries.filter((entry) => !allowed.has(entry.name));
  await Promise.all(
    unexpected.map((entry) =>
      rm(path.join(outputDirectory, entry.name), { recursive: true, force: true }),
    ),
  );
  if (unexpected.length > 0) {
    throw new ArtifactPrivacyError(["unexpected_playwright_artifact"]);
  }
}
