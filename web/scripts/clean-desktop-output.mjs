/** 清空 Electron main/preload 的编译目录，防止已删除模块残留进安装包。 */

import { rm } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const webRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
await rm(path.join(webRoot, "desktop-dist"), { recursive: true, force: true });
