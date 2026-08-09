/** 用历史 React 状态同步缺陷证明正式 ESLint 配置会返回非零。 */

import { spawnSync } from "node:child_process";
import { mkdtemp, readFile, rm, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";

const webRoot = process.cwd();
const temporaryRoots = [];

afterEach(async () => {
  await Promise.all(
    temporaryRoots.splice(0).map((path) => rm(path, { force: true, recursive: true })),
  );
});

describe("frontend ESLint gate", () => {
  it("rejects state synchronization inside an effect", async () => {
    const root = await mkdtemp(join(tmpdir(), "trowel-eslint-"));
    temporaryRoots.push(root);
    await writeFile(join(root, "package.json"), '{"type":"module"}\n');
    await symlink(join(webRoot, "node_modules"), join(root, "node_modules"), "dir");
    const configPath = join(root, "eslint.config.js");
    await writeFile(
      configPath,
      await readFile(join(webRoot, "eslint.config.js"), "utf8"),
    );
    const sourcePath = join(root, "BadComponent.tsx");
    await writeFile(
      sourcePath,
      'import { useEffect, useState } from "react";\n' +
        "export function BadComponent() {\n" +
        "  const [value, setValue] = useState(0);\n" +
        "  useEffect(() => { setValue(1); }, []);\n" +
        "  return <span>{value}</span>;\n" +
        "}\n",
    );

    const result = spawnSync(
      join(webRoot, "node_modules", ".bin", "eslint"),
      ["--config", "eslint.config.js", "--max-warnings=0", "BadComponent.tsx"],
      { cwd: root, encoding: "utf8" },
    );

    expect(result.status).toBe(1);
    expect(result.stdout).toContain("react-hooks/set-state-in-effect");
  });
});
