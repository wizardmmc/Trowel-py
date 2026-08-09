/** 验证分组 baseline 能展开稳定身份并拒绝宽泛或重复记录。 */

import { describe, expect, it } from "vitest";

import { parseBaseline } from "./baseline.mjs";

describe("parseBaseline", () => {
  it("把规则、路径和结构 subject 展开成精确 fingerprint", () => {
    const entries = parseBaseline({
      version: 1,
      groups: [
        {
          ruleId: "css/raw-color",
          path: "src/example.css",
          reason: "尚无稳定语义 token。",
          subjects: [
            "selector=.one|property=color|value=color=#fff",
            {
              subject: "selector=.two|property=color|value=color=#fff",
              count: 2,
            },
          ],
        },
      ],
    });

    expect(entries).toEqual([
      {
        fingerprint:
          "css/raw-color|src/example.css|selector=.one|property=color|value=color=#fff",
        count: 1,
      },
      {
        fingerprint:
          "css/raw-color|src/example.css|selector=.two|property=color|value=color=#fff",
        count: 2,
      },
    ]);
  });

  it("拒绝 glob、缺少理由和重复 subject", () => {
    expect(() => parseBaseline({
      version: 1,
      groups: [
        {
          ruleId: "css/raw-color",
          path: "src/**/*.css",
          reason: "",
          subjects: ["same", "same"],
        },
      ],
    })).toThrow(/精确文件|理由|重复/u);
  });
});
