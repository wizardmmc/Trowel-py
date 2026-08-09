/** 使用 PostCSS AST 检查颜色、token fallback 和层叠契约。 */

import postcss from "postcss";
import valueParser from "postcss-value-parser";

import { findColorLiterals } from "./color-literals.mjs";
import { createDiagnostic, normalizeContractPath } from "./diagnostic.mjs";
import {
  isFallbackOwned,
  isGlobalLayerOwned,
  isRawColorDeclarationOwned,
} from "./policy.mjs";

const NAMED_COLOR_PROPERTIES = new Set([
  "accent-color",
  "background",
  "box-shadow",
  "caret-color",
  "color",
  "fill",
  "filter",
  "flood-color",
  "lighting-color",
  "scrollbar-color",
  "stop-color",
  "stroke",
  "text-shadow",
]);
const NAMED_COLOR_PROPERTY_PREFIXES = [
  "background-",
  "border",
  "column-rule",
  "outline",
  "text-decoration",
  "text-emphasis",
  "-webkit-text-stroke",
];

/** 判断属性值中的 CSS 颜色名是否具有颜色语义，而非动画名等普通标识符。 */
function acceptsNamedColor(property) {
  const normalized = property.toLowerCase();
  return (
    normalized.startsWith("--") ||
    NAMED_COLOR_PROPERTIES.has(normalized) ||
    NAMED_COLOR_PROPERTY_PREFIXES.some((prefix) =>
      normalized.startsWith(prefix)
    )
  );
}

/** 返回声明值中的 var() 名称及其是否包含 fallback。 */
function variableReferences(value) {
  const references = [];
  valueParser(value).walk((node) => {
    if (node.type !== "function" || node.value.toLowerCase() !== "var") {
      return undefined;
    }
    const token = node.nodes.find((child) => child.type === "word")?.value;
    if (token) {
      references.push({
        token,
        hasFallback: node.nodes.some(
          (child) => child.type === "div" && child.value === ",",
        ),
      });
    }
    return undefined;
  });
  return references;
}

/** 返回声明所在的媒体查询等结构上下文。 */
function atRuleContext(declaration) {
  const contexts = [];
  for (let node = declaration.parent?.parent; node; node = node.parent) {
    if (node.type === "atrule") {
      contexts.unshift(`@${node.name} ${node.params}`.trim());
    }
  }
  return contexts.join(" > ");
}

/** 构造不会随普通换行变化的 CSS 声明身份。 */
function declarationSubject(declaration, value) {
  const selector = declaration.parent?.selector ?? "<unknown>";
  const context = atRuleContext(declaration);
  return [
    context ? `context=${context}` : "",
    `selector=${selector}`,
    `property=${declaration.prop}`,
    `value=${value}`,
  ].filter(Boolean).join("|");
}

/** 判断局部层级 owner 是否在真实 CSS 中建立了隔离范围。 */
function activeLocalLayerOwner(root, filePath, selector, policy) {
  const normalizedPath = normalizeContractPath(filePath);
  return (policy.localLayerOwners ?? []).find((entry) => {
    if (
      normalizeContractPath(entry.path) !== normalizedPath ||
      !selector.startsWith(entry.selectorPrefix)
    ) {
      return false;
    }
    let isolated = false;
    root.walkRules(entry.rootSelector, (rule) => {
      rule.walkDecls("isolation", (declaration) => {
        if (declaration.value.trim() === "isolate") isolated = true;
      });
    });
    return isolated;
  });
}

/** 返回局部 owner 根选择器中定义的命名层级变量。 */
function localLayerVariables(root, owner) {
  const variables = new Set();
  if (!owner) return variables;
  root.walkRules(owner.rootSelector, (rule) => {
    rule.walkDecls(/^--/u, (declaration) => {
      if (/^-?\d+$/u.test(declaration.value.trim())) {
        variables.add(declaration.prop);
      }
    });
  });
  return variables;
}

/** 判断 z-index 是否符合全局 token 或登记后的局部层级语义。 */
function isAllowedZIndex({ root, declaration, designTokens, filePath, policy }) {
  const value = declaration.value.trim();
  if (value === "auto") return true;

  const selector = declaration.parent?.selector ?? "";
  const selectors = declaration.parent?.selectors ?? [selector];
  const localOwner = activeLocalLayerOwner(root, filePath, selector, policy);
  if ((value === "0" || value === "1") && localOwner) return true;

  const parsed = valueParser(value).nodes.filter((node) => node.type !== "space");
  if (parsed.length !== 1 || parsed[0].type !== "function" || parsed[0].value !== "var") {
    return false;
  }
  const [reference] = variableReferences(value);
  if (!reference || reference.hasFallback) return false;
  if (reference.token.startsWith("--layer-")) {
    return (
      designTokens.has(reference.token) &&
      isGlobalLayerOwned({
        filePath,
        selectors,
        variable: reference.token,
        globalLayerOwners: policy.globalLayerOwners,
      })
    );
  }
  return localLayerVariables(root, localOwner).has(reference.token);
}

/** 分析一份 CSS 源码，返回不执行 policy/baseline 对账的原始诊断。 */
export function analyzeCss({
  filePath,
  sourceText,
  designTokens,
  policy,
}) {
  let root;
  try {
    root = postcss.parse(sourceText, { from: filePath });
  } catch (error) {
    return [createDiagnostic({
      ruleId: "css/parse-error",
      filePath,
      line: error.line ?? 1,
      column: error.column ?? 1,
      subject: "invalid-css",
      message: `CSS 无法解析：${error.reason ?? error.message}`,
    })];
  }

  const diagnostics = [];
  root.walkDecls((declaration) => {
    const line = declaration.source?.start?.line ?? 1;
    const column = declaration.source?.start?.column ?? 1;
    for (const reference of variableReferences(declaration.value)) {
      if (reference.hasFallback && designTokens.has(reference.token)) {
        diagnostics.push(createDiagnostic({
          ruleId: "css/design-token-fallback",
          filePath,
          line,
          column,
          subject: declarationSubject(
            declaration,
            `${reference.token}|${declaration.value}`,
          ),
          message: `${reference.token} 是 Trowel 设计 token，不能用 fallback 掩盖缺失或改名`,
        }));
      } else if (
        reference.hasFallback &&
        !isFallbackOwned({
          filePath,
          variable: reference.token,
          fallbackOwners: policy.fallbackOwners,
        })
      ) {
        diagnostics.push(createDiagnostic({
          ruleId: "css/unowned-fallback",
          filePath,
          line,
          column,
          subject: declarationSubject(
            declaration,
            `${reference.token}|${declaration.value}`,
          ),
          message: `${reference.token} 的 fallback 没有精确 runtime 或第三方 owner`,
        }));
      }
    }

    if (!isRawColorDeclarationOwned({
      filePath,
      property: declaration.prop,
      rawColorOwners: policy.rawColorOwners,
    })) {
      for (const color of findColorLiterals(declaration.value, {
        includeNamedColors: acceptsNamedColor(declaration.prop),
      })) {
        diagnostics.push(createDiagnostic({
          ruleId: "css/raw-color",
          filePath,
          line,
          column,
          subject: declarationSubject(declaration, `${declaration.prop}=${color}`),
          message: `裸颜色 ${color} 必须改用命名 token 或登记到精确颜色 owner`,
        }));
      }
    }

    if (
      declaration.prop === "z-index" &&
      !isAllowedZIndex({ root, declaration, designTokens, filePath, policy })
    ) {
      diagnostics.push(createDiagnostic({
        ruleId: "css/z-index",
        filePath,
        line,
        column,
        subject: declarationSubject(declaration, declaration.value.trim()),
        message: `z-index ${declaration.value.trim()} 没有明确的全局或局部层级 owner`,
      }));
    }
  });
  return diagnostics;
}
