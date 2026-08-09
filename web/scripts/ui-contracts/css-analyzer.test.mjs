/** 验证 CSS 客观契约分析器能区分违规、合法 owner 和稳定身份。 */

import { describe, expect, it } from "vitest";

import { analyzeCss } from "./css-analyzer.mjs";

const DESIGN_TOKENS = new Set([
  "--bg-card",
  "--garden-green",
  "--layer-dialog",
]);

const EMPTY_POLICY = {
  fallbackOwners: [],
  localLayerOwners: [],
  rawColorOwners: [],
};

/** 用最小公共输入分析一段 CSS。 */
function analyze(sourceText, overrides = {}) {
  return analyzeCss({
    filePath: "src/components/example.css",
    sourceText,
    designTokens: DESIGN_TOKENS,
    policy: EMPTY_POLICY,
    ...overrides,
  });
}

describe("analyzeCss", () => {
  it("拒绝 Trowel 设计 token fallback，但接受第三方运行时 fallback", () => {
    const diagnostics = analyze(
      `
        .card { color: var(--bg-card, #fff); }
        .menu { max-height: var(--radix-available-height, 320px); }
      `,
      {
        policy: {
          ...EMPTY_POLICY,
          fallbackOwners: [
            {
              path: "src/components/example.css",
              variable: "--radix-available-height",
              owner: "Radix 菜单布局",
              reason: "Radix 尚未测量可用高度时使用有界默认值。",
            },
          ],
        },
      },
    );

    expect(diagnostics.map((item) => item.ruleId)).toEqual([
      "css/design-token-fallback",
      "css/raw-color",
    ]);
  });

  it("拒绝没有精确 owner 的自有或未知变量 fallback", () => {
    const diagnostics = analyze(".error { color: var(--clay, #c45b4a); }");

    expect(diagnostics.map((item) => item.ruleId)).toEqual([
      "css/unowned-fallback",
      "css/raw-color",
    ]);
  });

  it("继续检查已登记 fallback 内嵌套的设计 token fallback", () => {
    const diagnostics = analyze(
      ".card { color: var(--runtime-color, var(--bg-card, #fff)); }",
      {
        policy: {
          ...EMPTY_POLICY,
          fallbackOwners: [
            {
              path: "src/components/example.css",
              variable: "--runtime-color",
              owner: "运行时颜色",
              reason: "运行时尚未写入时继续读取产品 token。",
            },
          ],
        },
      },
    );

    expect(diagnostics.map((item) => item.ruleId)).toEqual([
      "css/design-token-fallback",
      "css/raw-color",
    ]);
  });

  it("只允许明确 owner 在自定义属性中定义裸颜色", () => {
    const sourceText = `
      .chart {
        --chart-line: #4a7c59;
        color: #4a7c59;
      }
    `;
    const diagnostics = analyze(sourceText, {
      filePath: "src/statistics/ui/charts/chart-palette.css",
      policy: {
        ...EMPTY_POLICY,
        rawColorOwners: [
          {
            path: "src/statistics/ui/charts/chart-palette.css",
            declaration: "custom-property",
            owner: "Statistics 图表色板",
            reason: "ECharts 只从该色板读取图表专用颜色。",
          },
        ],
      },
    });

    expect(diagnostics).toHaveLength(1);
    expect(diagnostics[0].ruleId).toBe("css/raw-color");
    expect(diagnostics[0].subject).toContain("color=#4a7c59");
  });

  it("识别 CSS 命名颜色和现代颜色函数，但允许透明与 token 派生色", () => {
    const diagnostics = analyze(`
      .named { color: rebeccapurple; }
      .modern { color: oklch(60% 0.2 20); }
      .transparent { color: transparent; }
      .derived { color: color-mix(in srgb, var(--garden-green) 50%, transparent); }
      .relative { color: rgb(from var(--garden-green) r g b / 50%); }
    `);

    expect(diagnostics.map((item) => item.ruleId)).toEqual([
      "css/raw-color",
      "css/raw-color",
    ]);
  });

  it("不把颜色同名的非颜色 CSS 标识符判成裸颜色", () => {
    expect(analyze(".pulse { animation: red 1s; }")).toHaveLength(0);
  });

  it("相对颜色只在来源本身是具体颜色时报告", () => {
    const diagnostics = analyze(`
      .token-source { color: rgb(from var(--garden-green) r g b); }
      .literal-source { color: rgb(from #fff r g b); }
    `);

    expect(diagnostics).toEqual([
      expect.objectContaining({
        ruleId: "css/raw-color",
        message: expect.stringContaining("#fff"),
      }),
    ]);
  });

  it("拒绝任意 z-index，接受全局 token 和登记后的隔离局部层级", () => {
    const policy = {
      ...EMPTY_POLICY,
      globalLayerOwners: [
        {
          path: "src/components/workdir.css",
          selector: ".global-dialog",
          variable: "--layer-dialog",
          owner: "全局对话框",
          reason: "对话框通过 portal 挂到 document.body。",
        },
      ],
      localLayerOwners: [
        {
          path: "src/components/workdir.css",
          rootSelector: ".workdir",
          selectorPrefix: ".workdir",
          owner: "工作目录选择器",
          reason: "输入、影子文本和菜单只在组件内部排序。",
        },
      ],
    };
    const diagnostics = analyzeCss({
      filePath: "src/components/workdir.css",
      sourceText: `
        .workdir { isolation: isolate; --workdir-layer-menu: 2; }
        .workdir__input { position: relative; z-index: 0; }
        .workdir__menu { position: absolute; z-index: var(--workdir-layer-menu); }
        .global-dialog { position: fixed; z-index: var(--layer-dialog); }
        .borrowed-dialog-layer { position: relative; z-index: var(--layer-dialog); }
        .unknown { position: fixed; z-index: 9999; }
      `,
      designTokens: DESIGN_TOKENS,
      policy,
    });

    expect(diagnostics).toHaveLength(2);
    expect(diagnostics.every((item) => item.ruleId === "css/z-index")).toBe(true);
    expect(diagnostics.map((item) => item.subject).join("\n")).toContain("9999");
  });

  it("结构身份不随换行变化", () => {
    const compact = analyze(".card { color: #fff; }")[0];
    const expanded = analyze("\n\n.card {\n  color: #fff;\n}\n")[0];

    expect(compact.fingerprint).toBe(expanded.fingerprint);
    expect(compact.line).not.toBe(expanded.line);
  });
});
