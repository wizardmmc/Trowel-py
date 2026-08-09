/** 验证 JSX 客观契约分析器按语法结构识别原生控件和颜色 owner。 */

import { describe, expect, it } from "vitest";

import { analyzeJsx } from "./jsx-analyzer.mjs";

const EMPTY_POLICY = {
  fallbackOwners: [],
  rawColorOwners: [],
};

/** 用最小公共输入分析一段 TSX。 */
function analyze(sourceText, overrides = {}) {
  return analyzeJsx({
    filePath: "src/components/Example.tsx",
    sourceText,
    policy: EMPTY_POLICY,
    ...overrides,
  });
}

describe("analyzeJsx", () => {
  it("拒绝真实 select/option，忽略注释和字符串中的相同文本", () => {
    const diagnostics = analyze(`
      /** 文档示例：<select><option /></select> */
      const example = "<select>";
      export function Example() {
        return <select><option value="a">A</option></select>;
      }
    `);

    expect(diagnostics.filter((item) => item.ruleId === "jsx/native-select"))
      .toHaveLength(2);
  });

  it("文件输入只有 hidden 属性存在时才通过", () => {
    const diagnostics = analyze(`
      export function Example({ hidden }: { hidden: boolean }) {
        return <>
          <input type="file" />
          <input type={"file"} hidden={hidden} />
          <input type={"file"} hidden />
          <input type={"file" as const} />
        </>;
      }
    `);

    expect(diagnostics.filter((item) => item.ruleId === "jsx/visible-file-input"))
      .toHaveLength(3);
  });

  it("拒绝浏览器弹窗调用，但不误判同名本地函数", () => {
    const diagnostics = analyze(`
      function confirm(value: string) { return value; }
      confirm("local");
      function nested(alert: (value: string) => void) { alert("local"); }
      alert("browser");
      window.alert("browser");
      prompt("browser");
    `);

    expect(diagnostics.filter((item) => item.ruleId === "jsx/browser-dialog"))
      .toHaveLength(3);
  });

  it("局部绑定只遮蔽自身词法范围内的浏览器弹窗名", () => {
    const diagnostics = analyze(`
      try { throw new Error("test"); }
      catch (alert) { alert("local"); }
      alert("browser");
      {
        const window = { confirm() {} };
        window.confirm();
      }
      window.confirm("browser");
    `);

    expect(diagnostics.filter((item) => item.ruleId === "jsx/browser-dialog"))
      .toHaveLength(2);
  });

  it("只有 PopperSelect owner 可以直接导入 Radix Select", () => {
    const sourceText = `import * as SelectPrimitive from "@radix-ui/react-select";`;

    expect(analyze(sourceText)).toEqual([
      expect.objectContaining({ ruleId: "jsx/radix-select-owner" }),
    ]);
    expect(analyze(sourceText, {
      filePath: "src/components/ui/PopperSelect.tsx",
    })).toHaveLength(0);
  });

  it("拒绝普通 TSX 裸颜色，但允许明确的视觉资产 owner", () => {
    const sourceText = `export const color = "#4A7C59";`;

    expect(analyze(sourceText)).toEqual([
      expect.objectContaining({ ruleId: "jsx/raw-color" }),
    ]);
    expect(analyze(sourceText, {
      filePath: "src/components/pet/PetSVG.tsx",
      policy: {
        rawColorOwners: [
          {
            path: "src/components/pet/PetSVG.tsx",
            owner: "宠物 SVG 资产",
            reason: "插画颜色由该资产文件自身拥有。",
          },
        ],
      },
    })).toHaveLength(0);
  });

  it("只在颜色语义位置识别命名颜色", () => {
    const diagnostics = analyze(`
      const label = "red";
      const style = { color: "rebeccapurple" };
      export const icon = <path fill="white" />;
      export const expressionIcon = <path fill={"white"} />;
    `);

    expect(diagnostics.filter((item) => item.ruleId === "jsx/raw-color"))
      .toHaveLength(3);
  });

  it("把新增 switch 实现作为非阻断候选报告", () => {
    const diagnostics = analyze(`
      export function Example() {
        return <button role="switch" aria-checked={true} />;
      }
    `);

    expect(diagnostics).toEqual([
      expect.objectContaining({
        ruleId: "jsx/shared-switch-candidate",
        severity: "info",
      }),
    ]);
  });
});
