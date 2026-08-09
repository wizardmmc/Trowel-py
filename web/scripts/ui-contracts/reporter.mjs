/** 把 UI contract 结果渲染成稳定、可定位的命令行反馈。 */

/** 按路径、位置和规则稳定排列诊断。 */
function sortedDiagnostics(diagnostics) {
  return [...diagnostics].sort((left, right) =>
    left.filePath.localeCompare(right.filePath) ||
    left.line - right.line ||
    left.column - right.column ||
    left.ruleId.localeCompare(right.ruleId),
  );
}

/** 渲染一条可由编辑器和 Agent 直接定位的诊断。 */
function diagnosticLine(diagnostic) {
  return `${diagnostic.filePath}:${diagnostic.line}:${diagnostic.column} [${diagnostic.ruleId}] ${diagnostic.message}`;
}

/** 输出检查结果并返回 CLI 应使用的退出码。 */
export function reportUiContractResult(result, output = console) {
  if (result.infos.length > 0) {
    output.log("前端共享组件候选（仅报告，不阻断）：");
    for (const diagnostic of sortedDiagnostics(result.infos)) {
      output.log(`- ${diagnosticLine(diagnostic)}`);
    }
  }
  if (result.errors.length > 0) {
    output.error("前端客观规范门禁失败：");
    for (const diagnostic of sortedDiagnostics(result.errors)) {
      output.error(`- ${diagnosticLine(diagnostic)}`);
    }
    return 1;
  }
  output.log(
    `前端客观规范门禁通过：扫描 ${result.scannedFiles} 个生产源码文件，` +
      `接受 ${result.baselineEntries} 条精确旧债。`,
  );
  return 0;
}
