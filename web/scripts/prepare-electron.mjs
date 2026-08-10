/** 下载并核验当前锁文件对应的 Electron 可执行文件。 */

import { constants } from "node:fs";
import { access } from "node:fs/promises";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const electronBinary = require("electron");

await access(electronBinary, constants.X_OK);
process.stdout.write(`Electron executable ready: ${electronBinary}\n`);
