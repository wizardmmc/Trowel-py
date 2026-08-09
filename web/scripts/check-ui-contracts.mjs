/** 运行 Trowel 前端颜色、层级、原生控件和共享 owner 的客观规范门禁。 */

import { readFile } from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

import { reportUiContractResult } from "./ui-contracts/reporter.mjs";
import { runUiContracts } from "./ui-contracts/runner.mjs";
import { UI_CONTRACT_POLICY } from "./ui-contracts/project-policy.mjs";
import { parseBaseline } from "./ui-contracts/baseline.mjs";

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url));
const sourceRoot = path.resolve(scriptDirectory, "../src");
const baselinePath = path.join(scriptDirectory, "ui-contracts/baseline.json");
const baseline = parseBaseline(JSON.parse(await readFile(baselinePath, "utf8")));
const result = await runUiContracts({
  sourceRoot,
  policy: UI_CONTRACT_POLICY,
  baseline,
});
process.exitCode = reportUiContractResult(result);
