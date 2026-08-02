/** 检查前端生产 TypeScript 模块是否以文件级职责说明开头。 */

import { readFile, readdir } from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const sourceRoot = path.resolve(scriptDir, "../src");
const sourceExtensions = new Set([".ts", ".tsx"]);

function isProductionModule(relativePath) {
  const segments = relativePath.split("/");
  const basename = segments.at(-1) ?? "";
  return (
    !segments.includes("__tests__") &&
    !segments.includes("generated") &&
    !basename.includes(".test.") &&
    !basename.includes(".spec.") &&
    !basename.endsWith(".d.ts") &&
    !basename.endsWith(".generated.ts") &&
    !basename.endsWith(".generated.tsx") &&
    basename !== "test-setup.ts"
  );
}

async function collectModules(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  const modules = [];
  for (const entry of entries) {
    const fullPath = path.join(directory, entry.name);
    if (entry.isDirectory()) {
      modules.push(...(await collectModules(fullPath)));
    } else if (sourceExtensions.has(path.extname(entry.name))) {
      modules.push(fullPath);
    }
  }
  return modules;
}

const modules = (await collectModules(sourceRoot)).filter((filePath) =>
  isProductionModule(path.relative(sourceRoot, filePath).split(path.sep).join("/")),
);
const missing = [];
for (const filePath of modules) {
  const source = await readFile(filePath, "utf8");
  if (!source.startsWith("/**")) {
    missing.push(path.relative(sourceRoot, filePath).split(path.sep).join("/"));
  }
}

if (missing.length > 0) {
  console.error("前端生产模块缺少文件级职责说明：");
  for (const filePath of missing.sort()) console.error(`- ${filePath}`);
  process.exit(1);
}

console.log(`前端模块说明检查通过（${modules.length} 个生产模块）。`);
