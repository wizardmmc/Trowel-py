/** 使用 TypeScript AST 检查 JSX 原生控件、共享 owner 和裸颜色。 */

import ts from "typescript";

import { findColorLiterals } from "./color-literals.mjs";
import { createDiagnostic } from "./diagnostic.mjs";
import { isRawColorSourceOwned } from "./policy.mjs";

const BROWSER_DIALOGS = new Set(["alert", "confirm", "prompt"]);
const POPPER_SELECT_OWNER = "src/components/ui/PopperSelect.tsx";
const COLOR_SEMANTIC_NAMES = new Set([
  "background",
  "border",
  "boxshadow",
  "fill",
  "outline",
  "stroke",
  "textshadow",
]);

/** 去掉不改变运行时值的 TypeScript 表达式包装。 */
function unwrapStaticExpression(expression) {
  if (
    ts.isAsExpression(expression) ||
    ts.isNonNullExpression(expression) ||
    ts.isParenthesizedExpression(expression) ||
    ts.isSatisfiesExpression(expression) ||
    ts.isTypeAssertionExpression(expression)
  ) {
    return unwrapStaticExpression(expression.expression);
  }
  return expression;
}

/** 返回表达式可以静态确定的字符串值。 */
function staticStringText(expression) {
  const unwrapped = unwrapStaticExpression(expression);
  return ts.isStringLiteralLike(unwrapped) ? unwrapped.text : null;
}

/** 返回 JSX 属性的静态字符串；动态表达式保持未知。 */
function jsxAttributeText(node, attributeName) {
  const attribute = node.attributes.properties.find(
    (property) =>
      ts.isJsxAttribute(property) && property.name.text === attributeName,
  );
  if (!attribute || !ts.isJsxAttribute(attribute) || !attribute.initializer) {
    return null;
  }
  if (ts.isStringLiteral(attribute.initializer)) return attribute.initializer.text;
  if (
    ts.isJsxExpression(attribute.initializer) &&
    attribute.initializer.expression
  ) {
    return staticStringText(attribute.initializer.expression);
  }
  return null;
}

/** 判断 JSX boolean 属性是否明确为 true。 */
function hasTrueJsxAttribute(node, attributeName) {
  const attribute = node.attributes.properties.find(
    (property) =>
      ts.isJsxAttribute(property) && property.name.text === attributeName,
  );
  if (!attribute || !ts.isJsxAttribute(attribute)) return false;
  if (!attribute.initializer) return true;
  return (
    ts.isJsxExpression(attribute.initializer) &&
    attribute.initializer.expression?.kind === ts.SyntaxKind.TrueKeyword
  );
}

/** 判断节点是否会建立运行时名字可见范围。 */
function isFunctionLikeScope(node) {
  return (
    ts.isArrowFunction(node) ||
    ts.isConstructorDeclaration(node) ||
    ts.isFunctionDeclaration(node) ||
    ts.isFunctionExpression(node) ||
    ts.isGetAccessorDeclaration(node) ||
    ts.isMethodDeclaration(node) ||
    ts.isSetAccessorDeclaration(node)
  );
}

/** 返回块级声明所属的最近词法范围。 */
function nearestLexicalScope(node) {
  for (let current = node; current; current = current.parent) {
    if (
      ts.isSourceFile(current) ||
      ts.isBlock(current) ||
      ts.isCaseBlock(current) ||
      ts.isCatchClause(current) ||
      ts.isForStatement(current) ||
      ts.isForInStatement(current) ||
      ts.isForOfStatement(current) ||
      isFunctionLikeScope(current)
    ) {
      return current;
    }
  }
  return null;
}

/** 返回 `var` 声明所属的最近函数或文件范围。 */
function nearestFunctionScope(node) {
  for (let current = node; current; current = current.parent) {
    if (ts.isSourceFile(current) || isFunctionLikeScope(current)) return current;
  }
  return null;
}

/** 按词法范围收集运行时绑定，避免无关嵌套声明放行浏览器弹窗。 */
function collectScopeBindings(sourceFile) {
  const bindings = new Map();
  const addBinding = (scope, name) => {
    if (!scope) return;
    const names = bindings.get(scope) ?? new Set();
    if (ts.isIdentifier(name)) names.add(name.text);
    if (ts.isObjectBindingPattern(name) || ts.isArrayBindingPattern(name)) {
      for (const element of name.elements) {
        if (ts.isBindingElement(element)) addBinding(scope, element.name);
      }
    }
    bindings.set(scope, names);
  };
  const visit = (node) => {
    if (
      (ts.isFunctionDeclaration(node) || ts.isClassDeclaration(node)) &&
      node.name
    ) {
      addBinding(nearestLexicalScope(node.parent), node.name);
    }
    if (ts.isFunctionExpression(node) && node.name) {
      addBinding(node, node.name);
    }
    if (ts.isVariableDeclaration(node) && !ts.isCatchClause(node.parent)) {
      const declarationList = node.parent;
      const isBlockScoped =
        ts.isVariableDeclarationList(declarationList) &&
        (declarationList.flags & ts.NodeFlags.BlockScoped) !== 0;
      addBinding(
        isBlockScoped
          ? nearestLexicalScope(declarationList.parent)
          : nearestFunctionScope(declarationList.parent),
        node.name,
      );
    }
    if (ts.isParameter(node)) {
      addBinding(nearestFunctionScope(node.parent), node.name);
    }
    if (ts.isCatchClause(node) && node.variableDeclaration) {
      addBinding(node, node.variableDeclaration.name);
    }
    if (ts.isImportClause(node) && node.name) addBinding(sourceFile, node.name);
    if (ts.isImportSpecifier(node)) addBinding(sourceFile, node.name);
    if (ts.isNamespaceImport(node)) addBinding(sourceFile, node.name);
    ts.forEachChild(node, visit);
  };
  visit(sourceFile);
  return bindings;
}

/** 判断调用点向上的可见范围是否声明了同名运行时绑定。 */
function isNameShadowed(node, name, scopeBindings) {
  for (let current = node.parent; current; current = current.parent) {
    if (scopeBindings.get(current)?.has(name)) return true;
  }
  return false;
}

/** 返回 AST 节点的一行一列位置。 */
function nodePosition(sourceFile, node) {
  const position = sourceFile.getLineAndCharacterOfPosition(node.getStart(sourceFile));
  return { line: position.line + 1, column: position.character + 1 };
}

/** 返回颜色字符串所在的属性或变量语义。 */
function colorContext(node, sourceFile) {
  const parent = semanticParent(node);
  if (ts.isJsxAttribute(parent)) return `jsx-attribute=${parent.name.text}`;
  if (ts.isPropertyAssignment(parent)) {
    return `property=${parent.name.getText(sourceFile)}`;
  }
  if (ts.isVariableDeclaration(parent)) {
    return `variable=${parent.name.getText(sourceFile)}`;
  }
  return `syntax=${ts.SyntaxKind[parent.kind]}`;
}

/** 跳过 JSX 和 TypeScript 中不改变字符串业务语义的父包装。 */
function semanticParent(node) {
  let current = node;
  while (current.parent) {
    const parent = current.parent;
    if (
      (ts.isJsxExpression(parent) && parent.expression === current) ||
      ((ts.isAsExpression(parent) ||
        ts.isNonNullExpression(parent) ||
        ts.isParenthesizedExpression(parent) ||
        ts.isSatisfiesExpression(parent) ||
        ts.isTypeAssertionExpression(parent)) &&
        parent.expression === current)
    ) {
      current = parent;
      continue;
    }
    return parent;
  }
  return node.parent;
}

/** 返回字符串所在的 JSX、对象属性或变量名。 */
function colorSemanticName(node, sourceFile) {
  const parent = semanticParent(node);
  if (ts.isJsxAttribute(parent)) return parent.name.text;
  if (ts.isPropertyAssignment(parent)) {
    if (ts.isIdentifier(parent.name) || ts.isStringLiteralLike(parent.name)) {
      return parent.name.text;
    }
    return parent.name.getText(sourceFile);
  }
  if (ts.isVariableDeclaration(parent) && ts.isIdentifier(parent.name)) {
    return parent.name.text;
  }
  return null;
}

/** 判断字符串所在位置是否明确表示颜色或 SVG paint。 */
function isColorSemanticString(node, sourceFile) {
  const name = colorSemanticName(node, sourceFile);
  if (!name) return false;
  const normalized = name.replace(/[-_]/gu, "").toLowerCase();
  return normalized.endsWith("color") || COLOR_SEMANTIC_NAMES.has(normalized);
}

/** 判断调用是否明确指向浏览器原生 alert、confirm 或 prompt。 */
function browserDialogName(call, scopeBindings) {
  const expression = call.expression;
  if (
    ts.isIdentifier(expression) &&
    BROWSER_DIALOGS.has(expression.text) &&
    !isNameShadowed(expression, expression.text, scopeBindings)
  ) {
    return expression.text;
  }
  if (
    ts.isPropertyAccessExpression(expression) &&
    ts.isIdentifier(expression.expression) &&
    (expression.expression.text === "window" ||
      expression.expression.text === "globalThis") &&
    BROWSER_DIALOGS.has(expression.name.text) &&
    !isNameShadowed(
      expression.expression,
      expression.expression.text,
      scopeBindings,
    )
  ) {
    return expression.name.text;
  }
  if (
    ts.isElementAccessExpression(expression) &&
    ts.isIdentifier(expression.expression) &&
    (expression.expression.text === "window" ||
      expression.expression.text === "globalThis") &&
    expression.argumentExpression &&
    staticStringText(expression.argumentExpression) !== null &&
    BROWSER_DIALOGS.has(staticStringText(expression.argumentExpression)) &&
    !isNameShadowed(
      expression.expression,
      expression.expression.text,
      scopeBindings,
    )
  ) {
    return staticStringText(expression.argumentExpression);
  }
  return null;
}

/** 分析一份 TSX/TS 源码，返回不执行 policy/baseline 对账的原始诊断。 */
export function analyzeJsx({ filePath, sourceText, policy }) {
  const scriptKind = filePath.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS;
  const sourceFile = ts.createSourceFile(
    filePath,
    sourceText,
    ts.ScriptTarget.Latest,
    true,
    scriptKind,
  );
  const scopeBindings = collectScopeBindings(sourceFile);
  const ownsRawColors = isRawColorSourceOwned({
    filePath,
    rawColorOwners: policy.rawColorOwners,
  });
  const diagnostics = [];

  const report = (node, values) => {
    const position = nodePosition(sourceFile, node);
    diagnostics.push(createDiagnostic({
      filePath,
      line: position.line,
      column: position.column,
      ...values,
    }));
  };

  const visit = (node) => {
    if (ts.isImportDeclaration(node) && ts.isStringLiteral(node.moduleSpecifier)) {
      if (
        node.moduleSpecifier.text === "@radix-ui/react-select" &&
        filePath !== POPPER_SELECT_OWNER
      ) {
        report(node.moduleSpecifier, {
          ruleId: "jsx/radix-select-owner",
          subject: `import=${node.moduleSpecifier.text}`,
          message: "Radix Select 只能由 PopperSelect owner 直接导入",
        });
      }
    }

    if (ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) {
      const tagName = node.tagName.getText(sourceFile);
      if (tagName === "select" || tagName === "option") {
        report(node.tagName, {
          ruleId: "jsx/native-select",
          subject: `tag=${tagName}`,
          message: `生产 JSX 不能渲染原生 ${tagName}，请使用 PopperSelect`,
        });
      }
      if (
        tagName === "input" &&
        jsxAttributeText(node, "type") === "file" &&
        !hasTrueJsxAttribute(node, "hidden")
      ) {
        report(node.tagName, {
          ruleId: "jsx/visible-file-input",
          subject: "tag=input|type=file|hidden=false",
          message: "文件 input 必须隐藏，并由 Trowel 按钮触发",
        });
      }
      if (
        tagName === "button" &&
        jsxAttributeText(node, "role") === "switch"
      ) {
        report(node.tagName, {
          ruleId: "jsx/shared-switch-candidate",
          subject: "tag=button|role=switch",
          message: "发现 switch 实现候选，请人工判断是否与现有实现共享契约",
          severity: "info",
        });
      }
    }

    if (ts.isCallExpression(node)) {
      const dialogName = browserDialogName(node, scopeBindings);
      if (dialogName) {
        report(node.expression, {
          ruleId: "jsx/browser-dialog",
          subject: `call=${dialogName}`,
          message: `不能调用浏览器 ${dialogName}，请使用 Trowel 对话框`,
        });
      }
    }

    if (
      !ownsRawColors &&
      (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node))
    ) {
      for (const color of findColorLiterals(node.text, {
        includeNamedColors: isColorSemanticString(node, sourceFile),
      })) {
        report(node, {
          ruleId: "jsx/raw-color",
          subject: `${colorContext(node, sourceFile)}|value=${color}`,
          message: `裸颜色 ${color} 必须改用命名 token 或登记到精确颜色 owner`,
        });
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(sourceFile);
  return diagnostics;
}
