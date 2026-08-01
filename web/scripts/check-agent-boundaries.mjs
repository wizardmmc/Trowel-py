/** 检查前端 Agent 的所有权边界、已删除入口和相对 import 环。 */

import { access, readFile, readdir } from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

import ts from "typescript";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const sourceRoot = path.resolve(scriptDir, "../src");
const sourceExtensions = new Set([".ts", ".tsx"]);
const errors = [];

const requiredEntries = [
  "agent/domain/index.ts",
  "agent/application/index.ts",
  "agent/transport/index.ts",
  "agent/runtimes/index.ts",
  "agent/runtimes/shared/index.ts",
  "agent/runtimes/claude-code/index.ts",
  "agent/runtimes/codex/index.ts",
  "agent/ui/index.ts",
  "agent/index.ts",
];

const removedLegacyEntries = [
  "api/agent.ts",
  "api/agentTypes.ts",
  "api/ccStream.ts",
  "api/ccTypes.ts",
  "stores/ccFrameSelector.ts",
  "stores/ccReducer.ts",
  "stores/ccStore.ts",
  "components/cc/MessageList.tsx",
  "components/cc/SessionView.tsx",
  "components/cc/WorkdirPicker.tsx",
  "components/cc/ApprovalBlock.tsx",
  "components/cc/CodexCommandDialogs.tsx",
  "components/cc/CodexExplorationGroup.tsx",
  "components/cc/CodexMcpDetail.tsx",
  "components/cc/CodexWorkRail.tsx",
  "components/cc/ElicitationBlock.tsx",
  "components/cc/ElicitationControls.tsx",
  "components/cc/ToolCommandOutput.tsx",
  "components/cc/WorkflowPhaseRow.tsx",
  "components/cc/WorkflowSummary.tsx",
  "components/cc/WorkflowTree.tsx",
  "components/cc/codexCommandPresentation.ts",
  "components/cc/codexMcpPresentation.ts",
  "components/cc/pathDisplay.ts",
  "components/cc/useCodexCommandRoster.ts",
  "components/cc/useSessionLifecycle.ts",
];

const removedLegacyDirectories = ["stores/ccReducer", "stores/ccStore"];
const legacyImportPrefixes = [
  "api/agent",
  "api/agentTypes",
  "api/ccStream",
  "api/ccTypes",
  "stores/ccFrameSelector",
  "stores/ccReducer",
  "stores/ccStore",
  "components/cc/MessageList",
  "components/cc/SessionView",
  "components/cc/WorkdirPicker",
  "components/cc/ApprovalBlock",
  "components/cc/CodexCommandDialogs",
  "components/cc/CodexExplorationGroup",
  "components/cc/CodexMcpDetail",
  "components/cc/CodexWorkRail",
  "components/cc/ElicitationBlock",
  "components/cc/ElicitationControls",
  "components/cc/ToolCommandOutput",
  "components/cc/WorkflowPhaseRow",
  "components/cc/WorkflowSummary",
  "components/cc/WorkflowTree",
  "components/cc/codexCommandPresentation",
  "components/cc/codexMcpPresentation",
  "components/cc/pathDisplay",
  "components/cc/useCodexCommandRoster",
  "components/cc/useSessionLifecycle",
];

async function exists(filePath) {
  try {
    await access(filePath);
    return true;
  } catch {
    return false;
  }
}

async function collectSourceFiles(directory) {
  if (!(await exists(directory))) return [];
  const entries = await readdir(directory, { withFileTypes: true });
  const files = [];
  for (const entry of entries) {
    const fullPath = path.join(directory, entry.name);
    if (entry.isDirectory()) {
      files.push(...(await collectSourceFiles(fullPath)));
    } else if (sourceExtensions.has(path.extname(entry.name))) {
      files.push(fullPath);
    }
  }
  return files;
}

function relative(filePath) {
  return path.relative(sourceRoot, filePath).split(path.sep).join("/");
}

function parseSource(filePath, sourceText) {
  const kind = filePath.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS;
  return ts.createSourceFile(
    filePath,
    sourceText,
    ts.ScriptTarget.Latest,
    true,
    kind,
  );
}

function importRecords(sourceFile) {
  const records = [];
  for (const statement of sourceFile.statements) {
    if (ts.isImportDeclaration(statement)) {
      const specifier = statement.moduleSpecifier;
      if (!ts.isStringLiteral(specifier)) continue;
      const clause = statement.importClause;
      let typeOnly = clause?.isTypeOnly ?? false;
      if (
        !typeOnly &&
        clause?.namedBindings &&
        ts.isNamedImports(clause.namedBindings) &&
        !clause.name
      ) {
        typeOnly = clause.namedBindings.elements.every(
          (element) => element.isTypeOnly,
        );
      }
      records.push({ specifier: specifier.text, typeOnly });
    }
    if (ts.isExportDeclaration(statement) && statement.moduleSpecifier) {
      if (!ts.isStringLiteral(statement.moduleSpecifier)) continue;
      records.push({
        specifier: statement.moduleSpecifier.text,
        typeOnly: statement.isTypeOnly,
      });
    }
  }
  return records;
}

async function resolveRelativeImport(fromFile, specifier) {
  const base = path.resolve(path.dirname(fromFile), specifier);
  const candidates = path.extname(base)
    ? [base]
    : [
        `${base}.ts`,
        `${base}.tsx`,
        path.join(base, "index.ts"),
        path.join(base, "index.tsx"),
      ];
  for (const candidate of candidates) {
    if (await exists(candidate)) return candidate;
  }
  return null;
}

function agentLayer(filePath) {
  const file = relative(filePath);
  for (const layer of ["domain", "application", "transport", "runtimes", "ui"]) {
    if (file.startsWith(`agent/${layer}/`)) return layer;
  }
  return null;
}

function isLegacyImport(fromFile, targetFile) {
  if (relative(fromFile).includes("/__tests__/")) return false;
  const target = relative(targetFile).replace(/\.(ts|tsx)$/, "");
  return legacyImportPrefixes.some(
    (prefix) => target === prefix || target.startsWith(`${prefix}/`),
  );
}

function checkDomainSource(filePath, sourceFile, sourceText, records) {
  const forbiddenPackages = ["react", "zustand", "electron"];
  for (const { specifier, typeOnly } of records) {
    if (
      forbiddenPackages.some(
        (name) => specifier === name || specifier.startsWith(`${name}/`),
      )
    ) {
      errors.push(`${relative(filePath)}: domain 不得依赖 ${specifier}`);
    }
    if (specifier.includes("/agent/transport") && !typeOnly) {
      errors.push(
        `${relative(filePath)}: domain 只能用 import type 读取 transport DTO`,
      );
    }
  }
  const forbiddenRuntimeNames = ["fetch", "EventSource", "ReadableStream"];
  for (const name of forbiddenRuntimeNames) {
    if (new RegExp(`\\b${name}\\b`).test(sourceText)) {
      errors.push(`${relative(filePath)}: domain 不得使用 ${name}`);
    }
  }
  if (sourceText.includes("text/event-stream")) {
    errors.push(`${relative(filePath)}: domain 不得解释 SSE`);
  }

  const visit = (node) => {
    if (
      ts.isIdentifier(node) &&
      node.text === "Electron" &&
      !ts.isImportSpecifier(node)
    ) {
      errors.push(`${relative(filePath)}: domain 不得引用 Electron`);
    }
    ts.forEachChild(node, visit);
  };
  visit(sourceFile);
}

async function checkRequiredLayout() {
  for (const entry of requiredEntries) {
    if (!(await exists(path.join(sourceRoot, entry)))) {
      errors.push(`${entry}: 缺少 Agent owner 入口`);
    }
  }
}

async function checkRemovedLegacyLayout() {
  for (const entry of removedLegacyEntries) {
    const filePath = path.join(sourceRoot, entry);
    if (await exists(filePath)) {
      errors.push(`${entry}: L05 后不得恢复旧 Agent import 入口`);
    }
  }
  for (const directory of removedLegacyDirectories) {
    if (await exists(path.join(sourceRoot, directory))) {
      errors.push(`${directory}: L05 后不得恢复旧 Agent facade 目录`);
    }
  }
}

function detectCycles(graph) {
  const visiting = new Set();
  const visited = new Set();
  const stack = [];

  function visit(filePath) {
    if (visiting.has(filePath)) {
      const start = stack.indexOf(filePath);
      const cycle = [...stack.slice(start), filePath].map(relative).join(" -> ");
      errors.push(`相对 import 出现循环: ${cycle}`);
      return;
    }
    if (visited.has(filePath)) return;
    visiting.add(filePath);
    stack.push(filePath);
    for (const dependency of graph.get(filePath) ?? []) visit(dependency);
    stack.pop();
    visiting.delete(filePath);
    visited.add(filePath);
  }

  for (const filePath of graph.keys()) visit(filePath);
}

await checkRequiredLayout();
await checkRemovedLegacyLayout();

const productionFiles = (await collectSourceFiles(sourceRoot)).filter(
  (filePath) => !relative(filePath).includes("__tests__/"),
);
const graph = new Map(productionFiles.map((filePath) => [filePath, []]));

for (const filePath of productionFiles) {
  const sourceText = await readFile(filePath, "utf8");
  const sourceFile = parseSource(filePath, sourceText);
  const records = importRecords(sourceFile);
  const layer = agentLayer(filePath);

  if (layer === "domain") {
    checkDomainSource(filePath, sourceFile, sourceText, records);
  }

  for (const record of records) {
    const { specifier } = record;
    if (!specifier.startsWith(".")) continue;
    const target = await resolveRelativeImport(filePath, specifier);
    if (!target) {
      if (!specifier.endsWith(".css")) {
        errors.push(`${relative(filePath)}: 无法解析 ${specifier}`);
      }
      continue;
    }
    if (graph.has(target)) graph.get(filePath).push(target);
    if (isLegacyImport(filePath, target)) {
      errors.push(
        `${relative(filePath)}: 生产代码仍从旧入口 ${relative(target)} 读取实现`,
      );
    }

    const targetPath = relative(target);
    if (
      !relative(filePath).startsWith("agent/runtimes/") &&
      (/^agent\/runtimes\/(claude-code|codex)\//).test(targetPath)
    ) {
      errors.push(
        `${relative(filePath)}: runtime 专属展示必须通过 agent/runtimes facade 导入`,
      );
    }

    if (layer === "ui" && relative(target).startsWith("api/")) {
      errors.push(`${relative(filePath)}: ui 应通过 application 读取 HTTP/SSE`);
    }

    const targetLayer = agentLayer(target);
    if (!layer || !targetLayer || layer === targetLayer) continue;
    if (layer === "transport") {
      errors.push(
        `${relative(filePath)}: transport 不得依赖 agent/${targetLayer}`,
      );
    } else if (layer === "domain") {
      if (targetLayer !== "transport" || !record.typeOnly) {
        errors.push(
          `${relative(filePath)}: domain 只能以类型依赖 transport，不能依赖 agent/${targetLayer}`,
        );
      }
    } else if (layer === "application" && targetLayer === "ui") {
      errors.push(`${relative(filePath)}: application 不得反向依赖 ui`);
    } else if (layer === "application" && targetLayer === "runtimes") {
      errors.push(`${relative(filePath)}: application 不得依赖 presentation adapter`);
    } else if (layer === "runtimes") {
      if (targetLayer === "application" || targetLayer === "ui") {
        errors.push(
          `${relative(filePath)}: runtimes 只能解释统一状态，不能依赖 agent/${targetLayer}`,
        );
      } else if (targetLayer === "transport" && !record.typeOnly) {
        errors.push(
          `${relative(filePath)}: runtimes 只能读取 transport 类型，不能发起传输`,
        );
      }
    } else if (layer === "ui" && targetLayer === "transport") {
      errors.push(`${relative(filePath)}: ui 应通过 application 读取 transport`);
    }
  }
}

detectCycles(graph);

if (errors.length > 0) {
  console.error("Agent 前端边界检查失败：");
  for (const error of [...new Set(errors)]) console.error(`- ${error}`);
  process.exit(1);
}

console.log(`Agent 前端边界检查通过（${productionFiles.length} 个生产模块，无循环）。`);
