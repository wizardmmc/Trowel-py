/** 解析按规则和文件分组的 UI contract 精确旧债。 */

import {
  normalizeContractPath,
  normalizeContractSubject,
} from "./diagnostic.mjs";

/** 判断 baseline 文件路径是否精确指向单个生产源码文件。 */
function isExactSourcePath(filePath) {
  return (
    filePath.startsWith("src/") &&
    !filePath.endsWith("/") &&
    !/[*?{}[\]]/u.test(filePath)
  );
}

/** 把可读分组 baseline 展开成 policy 状态机使用的 fingerprint 与数量。 */
export function parseBaseline(document) {
  if (document?.version !== 1 || !Array.isArray(document.groups)) {
    throw new Error("UI contract baseline 必须使用 version 1 分组格式");
  }
  const entries = [];
  const seen = new Set();
  for (const group of document.groups) {
    const filePath = normalizeContractPath(group.path ?? "");
    if (
      typeof group.ruleId !== "string" ||
      group.ruleId.length === 0 ||
      !isExactSourcePath(filePath) ||
      typeof group.reason !== "string" ||
      group.reason.trim().length === 0 ||
      !Array.isArray(group.subjects)
    ) {
      throw new Error("baseline 分组必须指定规则、精确文件、理由和 subject 列表");
    }
    for (const item of group.subjects) {
      const rawSubject = typeof item === "string" ? item : item?.subject;
      const count = typeof item === "string" ? 1 : item?.count;
      if (
        typeof rawSubject !== "string" ||
        rawSubject.length === 0 ||
        !Number.isInteger(count) ||
        count < 1
      ) {
        throw new Error("baseline subject 必须包含结构身份和合法数量");
      }
      const subject = normalizeContractSubject(rawSubject);
      const fingerprint = `${group.ruleId}|${filePath}|${subject}`;
      if (seen.has(fingerprint)) {
        throw new Error(`baseline 包含重复 subject：${fingerprint}`);
      }
      seen.add(fingerprint);
      entries.push({ fingerprint, count });
    }
  }
  return entries;
}
