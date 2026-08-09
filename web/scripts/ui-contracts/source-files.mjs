/** 收集 UI contract 使用的生产源码，并从 CSS AST 读取设计 token。 */

import { readFile, readdir } from "node:fs/promises";
import path from "node:path";

import postcss from "postcss";

const SOURCE_EXTENSIONS = new Set([".css", ".ts", ".tsx"]);

/** 判断相对路径是否属于需要检查的生产源码。 */
export function isUiContractSource(relativePath) {
  const normalized = relativePath.split(path.sep).join("/");
  const segments = normalized.split("/");
  const basename = segments.at(-1) ?? "";
  return (
    SOURCE_EXTENSIONS.has(path.extname(basename)) &&
    !segments.includes("__tests__") &&
    !segments.includes("generated") &&
    !basename.includes(".test.") &&
    !basename.includes(".spec.") &&
    !basename.endsWith(".d.ts") &&
    !basename.endsWith(".generated.ts") &&
    !basename.endsWith(".generated.tsx")
  );
}

/** 递归收集生产 CSS、TS 和 TSX 文件。 */
export async function collectUiContractSources(sourceRoot) {
  const files = [];
  const visit = async (directory) => {
    const entries = await readdir(directory, { withFileTypes: true });
    for (const entry of entries) {
      const filePath = path.join(directory, entry.name);
      if (entry.isDirectory()) {
        await visit(filePath);
        continue;
      }
      const relativePath = path.relative(sourceRoot, filePath);
      if (isUiContractSource(relativePath)) files.push(filePath);
    }
  };
  await visit(sourceRoot);
  return files.sort();
}

/** 把源码绝对路径映射成 policy 和 baseline 使用的 src/ 相对路径。 */
export function contractPath(sourceRoot, filePath) {
  const relativePath = path.relative(sourceRoot, filePath).split(path.sep).join("/");
  return `src/${relativePath}`;
}

/** 从 tokens.css 的自定义属性声明读取 Trowel 设计 token 名称。 */
export async function readDesignTokens(tokensFile) {
  const sourceText = await readFile(tokensFile, "utf8");
  const root = postcss.parse(sourceText, { from: tokensFile });
  const tokens = new Set();
  root.walkDecls(/^--/u, (declaration) => tokens.add(declaration.prop));
  return tokens;
}
