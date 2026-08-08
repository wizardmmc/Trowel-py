/** 阻止生产界面重新引入原生下拉框、文件控件或浏览器弹窗。 */

import { readFile, readdir } from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url));
const sourceRoot = path.resolve(scriptDirectory, "../src");

/** 收集生产 JSX 模块；测试和声明文件不参与产品界面契约。 */
async function collectProductionTsx(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  const files = [];
  for (const entry of entries) {
    const fullPath = path.join(directory, entry.name);
    if (entry.isDirectory()) {
      if (entry.name !== "__tests__") {
        files.push(...(await collectProductionTsx(fullPath)));
      }
      continue;
    }
    if (
      entry.name.endsWith(".tsx") &&
      !entry.name.includes(".test.") &&
      !entry.name.includes(".spec.")
    ) {
      files.push(fullPath);
    }
  }
  return files;
}

const violations = [];
for (const filePath of await collectProductionTsx(sourceRoot)) {
  const source = await readFile(filePath, "utf8");
  const relativePath = path.relative(sourceRoot, filePath).split(path.sep).join("/");
  if (/<select(?:\s|>)/u.test(source) || /<option(?:\s|>)/u.test(source)) {
    violations.push(`${relativePath}: 使用 PopperSelect，不能渲染原生 select/option`);
  }
  if (/\b(?:window\.)?(?:alert|confirm|prompt)\s*\(/u.test(source)) {
    violations.push(`${relativePath}: 使用 Trowel 对话框，不能调用浏览器原生弹窗`);
  }

  for (const match of source.matchAll(/type=["']file["']/gu)) {
    const inputStart = source.lastIndexOf("<input", match.index);
    const inputEnd = source.indexOf("/>", match.index);
    const inputSource = source.slice(inputStart, inputEnd + 2);
    if (!/\bhidden\b/u.test(inputSource)) {
      violations.push(`${relativePath}: file input 必须隐藏，并由 Trowel 按钮触发`);
    }
  }
}

if (violations.length > 0) {
  console.error("前端共享控件契约检查失败：");
  for (const violation of violations) console.error(`- ${violation}`);
  process.exit(1);
}

console.log("前端共享控件契约检查通过：无原生下拉框、可见文件控件或浏览器弹窗。");
