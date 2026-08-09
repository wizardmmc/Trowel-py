/** 定义 UI contract 分析器共用的稳定诊断身份。 */

import path from "node:path";

/** 把平台路径统一成 baseline 使用的仓库相对路径。 */
export function normalizeContractPath(filePath) {
  return filePath.split(path.sep).join("/").replace(/^\.\//u, "");
}

/** 压缩不影响语义的空白，避免普通格式化使 baseline 身份漂移。 */
export function normalizeContractSubject(subject) {
  return subject.replace(/\s+/gu, " ").trim();
}

/** 创建带可读稳定身份的统一诊断。 */
export function createDiagnostic({
  ruleId,
  filePath,
  line,
  column,
  subject,
  message,
  severity = "error",
}) {
  const normalizedPath = normalizeContractPath(filePath);
  const normalizedSubject = normalizeContractSubject(subject);
  return {
    ruleId,
    filePath: normalizedPath,
    line,
    column,
    subject: normalizedSubject,
    fingerprint: `${ruleId}|${normalizedPath}|${normalizedSubject}`,
    severity,
    message,
  };
}
