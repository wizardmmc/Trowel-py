/** 校验 UI contract 的合法 owner，并把当前诊断与精确 baseline 对账。 */

import { createDiagnostic, normalizeContractPath } from "./diagnostic.mjs";

/** 判断字符串是否包含可追溯的 owner 或理由。 */
function hasExplanation(value) {
  return typeof value === "string" && value.trim().length > 0;
}

/** 校验 policy 只能指向精确文件，不能用目录或 glob 批量豁免。 */
function validateOwnerEntry(entry, label) {
  const normalizedPath = normalizeContractPath(entry.path ?? "");
  if (
    !normalizedPath.startsWith("src/") ||
    normalizedPath.endsWith("/") ||
    /[*?{}[\]]/u.test(normalizedPath) ||
    !hasExplanation(entry.owner) ||
    !hasExplanation(entry.reason)
  ) {
    throw new Error(`${label} 必须指定精确文件、owner 和理由`);
  }
}

/** 在 runner 启动前拒绝宽泛或缺少解释的 policy。 */
export function validatePolicy(policy) {
  for (const entry of policy.fallbackOwners ?? []) {
    validateOwnerEntry(entry, "fallback owner");
    if (typeof entry.variable !== "string" || !entry.variable.startsWith("--")) {
      throw new Error("fallback owner 必须指定精确 CSS 变量名");
    }
  }
  for (const entry of policy.rawColorOwners ?? []) {
    validateOwnerEntry(entry, "裸颜色 owner");
  }
  for (const entry of policy.globalLayerOwners ?? []) {
    validateOwnerEntry(entry, "全局层级 owner");
    if (
      !hasExplanation(entry.selector) ||
      typeof entry.variable !== "string" ||
      !entry.variable.startsWith("--layer-")
    ) {
      throw new Error("全局层级 owner 必须指定精确选择器和 --layer-* 变量");
    }
  }
  for (const entry of policy.localLayerOwners ?? []) {
    validateOwnerEntry(entry, "局部层级 owner");
    if (
      !hasExplanation(entry.rootSelector) ||
      !hasExplanation(entry.selectorPrefix)
    ) {
      throw new Error("局部层级 owner 必须指定根选择器和选择器前缀");
    }
  }
  for (const entry of policy.exceptions ?? []) {
    if (
      !hasExplanation(entry.fingerprint) ||
      !hasExplanation(entry.owner) ||
      !hasExplanation(entry.reason) ||
      (entry.count !== undefined &&
        (!Number.isInteger(entry.count) || entry.count < 1))
    ) {
      throw new Error("精确例外必须指定 fingerprint、owner、理由和合法数量");
    }
  }
}

/** 判断第三方或运行时变量是否获准在指定文件使用 fallback。 */
export function isFallbackOwned({
  filePath,
  variable,
  fallbackOwners = [],
}) {
  const normalizedPath = normalizeContractPath(filePath);
  return fallbackOwners.some(
    (entry) =>
      normalizeContractPath(entry.path) === normalizedPath &&
      entry.variable === variable,
  );
}

/** 判断某个声明是否由专门的裸颜色文件拥有。 */
export function isRawColorDeclarationOwned({
  filePath,
  property,
  rawColorOwners = [],
}) {
  const normalizedPath = normalizeContractPath(filePath);
  return rawColorOwners.some((entry) => {
    if (normalizeContractPath(entry.path) !== normalizedPath) return false;
    return entry.declaration !== "custom-property" || property.startsWith("--");
  });
}

/** 判断 TSX 文件是否整体拥有视觉资产或数据色板中的裸颜色。 */
export function isRawColorSourceOwned({ filePath, rawColorOwners = [] }) {
  const normalizedPath = normalizeContractPath(filePath);
  return rawColorOwners.some(
    (entry) =>
      normalizeContractPath(entry.path) === normalizedPath &&
      entry.declaration !== "custom-property",
  );
}

/** 判断全局层级 token 是否由指定文件和选择器精确拥有。 */
export function isGlobalLayerOwned({
  filePath,
  selectors,
  variable,
  globalLayerOwners = [],
}) {
  const normalizedPath = normalizeContractPath(filePath);
  return selectors.length > 0 && selectors.every(
    (selector) => globalLayerOwners.some(
      (entry) =>
        normalizeContractPath(entry.path) === normalizedPath &&
        entry.selector === selector &&
        entry.variable === variable,
    ),
  );
}

/** 按 fingerprint 汇总诊断，保留同一结构事实的重复数量。 */
function groupDiagnostics(diagnostics) {
  const grouped = new Map();
  for (const item of diagnostics) {
    const current = grouped.get(item.fingerprint) ?? [];
    current.push(item);
    grouped.set(item.fingerprint, current);
  }
  return grouped;
}

/** 创建提醒维护者删除已解决 baseline 的失败诊断。 */
function staleBaselineDiagnostic(entry) {
  return createDiagnostic({
    ruleId: "baseline/stale",
    filePath: "scripts/ui-contracts/baseline.json",
    line: 1,
    column: 1,
    subject: entry.fingerprint,
    message: "旧违规已经消失，请同步删除 baseline 记录",
  });
}

/** 把 error 与 baseline/例外对账，并保留不阻断的 info 候选。 */
export function evaluateDiagnostics({
  diagnostics,
  baseline = [],
  exceptions = [],
}) {
  const infos = diagnostics.filter((item) => item.severity === "info");
  const grouped = groupDiagnostics(
    diagnostics.filter((item) => item.severity !== "info"),
  );
  const baselineByFingerprint = new Map(
    baseline.map((entry) => [entry.fingerprint, entry]),
  );
  const exceptionByFingerprint = new Map(
    exceptions.map((entry) => [entry.fingerprint, entry]),
  );
  const errors = [];

  for (const [fingerprint, items] of grouped) {
    const exception = exceptionByFingerprint.get(fingerprint);
    const exceptionCount = exception?.count ?? (exception ? 1 : 0);
    const remaining = items.slice(exceptionCount);
    if (remaining.length === 0) continue;

    const baselineEntry = baselineByFingerprint.get(fingerprint);
    const baselineCount = baselineEntry?.count ?? 0;
    if (remaining.length > baselineCount) {
      errors.push(...remaining.slice(baselineCount));
    } else if (remaining.length < baselineCount) {
      errors.push(staleBaselineDiagnostic(baselineEntry));
    }
  }

  for (const entry of baseline) {
    const currentCount = grouped.get(entry.fingerprint)?.length ?? 0;
    const exception = exceptionByFingerprint.get(entry.fingerprint);
    const exceptionCount = exception?.count ?? (exception ? 1 : 0);
    if (currentCount - exceptionCount <= 0) {
      errors.push(staleBaselineDiagnostic(entry));
    }
  }

  return { errors, infos };
}
