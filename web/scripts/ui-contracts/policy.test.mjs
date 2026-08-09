/** 验证合法例外和只减不增 baseline 的判定状态机。 */

import { describe, expect, it } from "vitest";

import { evaluateDiagnostics, validatePolicy } from "./policy.mjs";

/** 构造一个具有稳定身份的测试诊断。 */
function diagnostic(fingerprint, severity = "error") {
  return {
    ruleId: "css/raw-color",
    filePath: "src/example.css",
    line: 1,
    column: 1,
    subject: "selector=.example|property=color|value=#fff",
    fingerprint,
    severity,
    message: "测试诊断",
  };
}

describe("evaluateDiagnostics", () => {
  it("接受匹配 baseline 的旧债，并保留 info 报告", () => {
    const result = evaluateDiagnostics({
      diagnostics: [diagnostic("known"), diagnostic("candidate", "info")],
      baseline: [{ fingerprint: "known", count: 1 }],
      exceptions: [],
    });

    expect(result.errors).toHaveLength(0);
    expect(result.infos).toEqual([diagnostic("candidate", "info")]);
  });

  it("拒绝 baseline 外的新违规", () => {
    const result = evaluateDiagnostics({
      diagnostics: [diagnostic("new")],
      baseline: [],
      exceptions: [],
    });

    expect(result.errors).toEqual([diagnostic("new")]);
  });

  it("baseline 项消失时要求删除陈旧记录", () => {
    const result = evaluateDiagnostics({
      diagnostics: [],
      baseline: [{ fingerprint: "resolved", count: 1 }],
      exceptions: [],
    });

    expect(result.errors).toEqual([
      expect.objectContaining({
        ruleId: "baseline/stale",
        subject: "resolved",
      }),
    ]);
  });

  it("只有精确且有 owner 和理由的 policy 例外可以放行", () => {
    const result = evaluateDiagnostics({
      diagnostics: [diagnostic("allowed")],
      baseline: [],
      exceptions: [
        {
          fingerprint: "allowed",
          owner: "KaTeX 渲染适配器",
          reason: "上游 errorColor 只接受具体颜色字符串。",
        },
      ],
    });

    expect(result.errors).toHaveLength(0);
  });
});

describe("validatePolicy", () => {
  it("拒绝目录、glob 和缺少理由的宽泛例外", () => {
    expect(() => validatePolicy({
      fallbackOwners: [],
      rawColorOwners: [
        { path: "src/components/**", owner: "组件", reason: "" },
      ],
      localLayerOwners: [],
      exceptions: [],
    })).toThrow(/精确文件|owner|理由/u);
  });
});
