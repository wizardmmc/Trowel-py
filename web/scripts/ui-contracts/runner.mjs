/** 组合文件系统适配器、纯分析器和 baseline/policy 对账。 */

import { readFile } from "node:fs/promises";
import path from "node:path";

import { analyzeCss } from "./css-analyzer.mjs";
import { analyzeJsx } from "./jsx-analyzer.mjs";
import { evaluateDiagnostics, validatePolicy } from "./policy.mjs";
import {
  collectUiContractSources,
  contractPath,
  readDesignTokens,
} from "./source-files.mjs";

/** 对指定源码树运行全部 UI contract，并返回统一结果。 */
export async function runUiContracts({ sourceRoot, policy, baseline }) {
  validatePolicy(policy);
  const tokensFile = path.join(sourceRoot, "styles/tokens.css");
  const [designTokens, files] = await Promise.all([
    readDesignTokens(tokensFile),
    collectUiContractSources(sourceRoot),
  ]);
  const diagnostics = (
    await Promise.all(files.map(async (filePath) => {
      const sourceText = await readFile(filePath, "utf8");
      const filePathForContract = contractPath(sourceRoot, filePath);
      if (filePath.endsWith(".css")) {
        return analyzeCss({
          filePath: filePathForContract,
          sourceText,
          designTokens,
          policy,
        });
      }
      return analyzeJsx({
        filePath: filePathForContract,
        sourceText,
        policy,
      });
    }))
  ).flat();
  const evaluated = evaluateDiagnostics({
    diagnostics,
    baseline,
    exceptions: policy.exceptions,
  });
  return {
    ...evaluated,
    diagnostics,
    scannedFiles: files.length,
    baselineEntries: baseline.length,
  };
}
