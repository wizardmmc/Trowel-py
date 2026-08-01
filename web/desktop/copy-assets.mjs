/** 把不经 TypeScript 编译的诊断页和 CommonJS 边界复制到桌面构建目录。 */

import { cp, mkdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const source = path.dirname(fileURLToPath(import.meta.url));
const outputRoot = path.resolve(source, "../desktop-dist");
const target = path.join(outputRoot, "desktop");
await mkdir(target, { recursive: true });
for (const name of [
  "diagnostic.html",
  "diagnostic.css",
  "diagnostic.js",
]) {
  await cp(path.join(source, name), path.join(target, name));
}
await cp(path.join(source, "package.json"), path.join(outputRoot, "package.json"));
