/** 验证 UI contract runner 的文件范围、依赖装配和统一结果。 */

import { mkdtemp, mkdir, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import { runUiContracts } from "./runner.mjs";

const temporaryRoots = [];

/** 创建包含 tokens.css 的最小前端源码树。 */
async function createSourceTree() {
  const root = await mkdtemp(path.join(os.tmpdir(), "trowel-ui-contracts-"));
  temporaryRoots.push(root);
  const sourceRoot = path.join(root, "src");
  await mkdir(path.join(sourceRoot, "styles"), { recursive: true });
  await writeFile(
    path.join(sourceRoot, "styles/tokens.css"),
    ":root { --bg-card: #fffdf7; --layer-dialog: 1000; }",
  );
  return sourceRoot;
}

/** 在临时源码树中写入一个生产或排除文件。 */
async function writeSource(sourceRoot, relativePath, sourceText) {
  const filePath = path.join(sourceRoot, relativePath);
  await mkdir(path.dirname(filePath), { recursive: true });
  await writeFile(filePath, sourceText);
}

afterEach(async () => {
  await Promise.all(
    temporaryRoots.splice(0).map((root) => rm(root, { recursive: true })),
  );
});

describe("runUiContracts", () => {
  it("组合 CSS/JSX 诊断并排除测试和 generated 文件", async () => {
    const sourceRoot = await createSourceTree();
    await writeSource(
      sourceRoot,
      "components/Bad.tsx",
      "export function Bad() { return <select><option>A</option></select>; }",
    );
    await writeSource(
      sourceRoot,
      "components/bad.css",
      ".bad { color: var(--bg-card, #fff); z-index: 9999; }",
    );
    await writeSource(
      sourceRoot,
      "__tests__/Ignored.test.tsx",
      "export const ignored = <select />;",
    );
    await writeSource(
      sourceRoot,
      "generated/Ignored.tsx",
      "export const ignored = <select />;",
    );

    const result = await runUiContracts({
      sourceRoot,
      policy: {
        fallbackOwners: [],
        rawColorOwners: [
          {
            path: "src/styles/tokens.css",
            declaration: "custom-property",
            owner: "全局设计 token",
            reason: "设计 token 是普通 UI 颜色的唯一事实源。",
          },
        ],
        localLayerOwners: [],
        exceptions: [],
      },
      baseline: [],
    });

    expect(result.errors.map((item) => item.ruleId).sort()).toEqual([
      "css/design-token-fallback",
      "css/raw-color",
      "css/z-index",
      "jsx/native-select",
      "jsx/native-select",
    ]);
    expect(result.scannedFiles).toBe(3);
    expect(result.errors.every((item) => !item.filePath.includes("Ignored")))
      .toBe(true);
  });
});
