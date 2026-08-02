/** 汇总 macOS 发布产物、依赖树、源码身份和 SHA-256 校验值。 */

import { execFile } from "node:child_process";
import { createHash } from "node:crypto";
import { copyFile, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import { promisify } from "node:util";
import { fileURLToPath } from "node:url";

const execute = promisify(execFile);
const webRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const projectRoot = path.resolve(webRoot, "..");
const packageJson = JSON.parse(await readFile(path.join(webRoot, "package.json"), "utf8"));
const version = packageJson.version;
const appPath = path.join(webRoot, "out", "Trowel-darwin-arm64", "Trowel.app");
const sourceDmg = path.join(webRoot, "out", "make", "Trowel.dmg");
const sourceZip = path.join(
  webRoot,
  "out",
  "make",
  "zip",
  "darwin",
  "arm64",
  `Trowel-darwin-arm64-${version}.zip`,
);
const releaseDirectory = path.join(webRoot, "out", "release");
const releaseNotesSource = path.join(
  webRoot,
  "packaging",
  `release-notes-v${version}.md`,
);
const buildSourcePaths = ["pyproject.toml", "uv.lock", "trowel_py", "web"];

/** 执行只读命令并返回去掉末尾空白的标准输出。 */
async function run(command, args, cwd = projectRoot) {
  const result = await execute(command, args, {
    cwd,
    encoding: "utf8",
    maxBuffer: 16 * 1024 * 1024,
  });
  return result.stdout.trim();
}

/** 计算一个文件的 SHA-256。 */
async function sha256(filePath) {
  const content = await readFile(filePath);
  return createHash("sha256").update(content).digest("hex");
}

await run("codesign", ["--verify", "--deep", "--strict", appPath]);
await run("hdiutil", ["verify", sourceDmg]);

const branch = await run("git", ["branch", "--show-current"]);
const commit = await run("git", ["rev-parse", "HEAD"]);
const trackedChanges = await run("git", [
  "status",
  "--porcelain",
  "--untracked-files=no",
]);
const buildSourceChanges = await run("git", [
  "status",
  "--porcelain",
  "--untracked-files=all",
  "--",
  ...buildSourcePaths,
]);
const pointedTags = (await run("git", ["tag", "--points-at", "HEAD"]))
  .split("\n")
  .filter(Boolean);

await rm(releaseDirectory, { recursive: true, force: true });
await mkdir(releaseDirectory, { recursive: true });

const stagedArtifacts = [
  {
    source: sourceDmg,
    name: `Trowel-${version}-macos-arm64.dmg`,
    kind: "installer",
  },
  {
    source: sourceZip,
    name: `Trowel-${version}-macos-arm64.zip`,
    kind: "app-archive",
  },
];
for (const artifact of stagedArtifacts) {
  artifact.path = path.join(releaseDirectory, artifact.name);
  await copyFile(artifact.source, artifact.path);
  artifact.sha256 = await sha256(artifact.path);
  delete artifact.source;
  delete artifact.path;
}

const pythonTree = await run("uv", ["tree", "--locked", "--no-dev"]);
const rawJavaScriptTree = await run("bun", ["pm", "ls", "--all"], webRoot);
const javaScriptTree = rawJavaScriptTree
  .split("\n")
  .filter((line, index) => index > 0 || !/\snode_modules\s*$/.test(line))
  .join("\n");
const lockHashes = {
  "uv.lock": await sha256(path.join(projectRoot, "uv.lock")),
  "web/bun.lock": await sha256(path.join(webRoot, "bun.lock")),
};
const dependenciesPath = path.join(releaseDirectory, "DEPENDENCIES.txt");
await writeFile(
  dependenciesPath,
  [
    `Trowel ${version} dependency manifest`,
    `uv.lock SHA-256: ${lockHashes["uv.lock"]}`,
    `web/bun.lock SHA-256: ${lockHashes["web/bun.lock"]}`,
    "",
    "[Python runtime dependency tree]",
    pythonTree,
    "",
    "[Electron and renderer dependency tree]",
    javaScriptTree,
    "",
  ].join("\n"),
  "utf8",
);

const manifestPath = path.join(releaseDirectory, "release-manifest.json");
await writeFile(
  manifestPath,
  `${JSON.stringify(
    {
      product: "Trowel",
      version,
      platform: "macos",
      architecture: "arm64",
      source: {
        commit,
        branch,
        tracked_changes: trackedChanges !== "",
        build_source_changes: buildSourceChanges !== "",
        version_tag_at_head: pointedTags.includes(`v${version}`),
      },
      signing: process.env.TROWEL_RELEASE_BUILD === "1" ? "developer-id" : "ad-hoc",
      notarized: process.env.TROWEL_RELEASE_BUILD === "1",
      lock_hashes: lockHashes,
      artifacts: stagedArtifacts,
    },
    null,
    2,
  )}\n`,
  "utf8",
);

const releaseNotesPath = path.join(releaseDirectory, "RELEASE_NOTES.md");
await copyFile(releaseNotesSource, releaseNotesPath);
const checksumFiles = [
  ...stagedArtifacts.map((artifact) => artifact.name),
  "DEPENDENCIES.txt",
  "release-manifest.json",
  "RELEASE_NOTES.md",
];
const checksumLines = [];
for (const name of checksumFiles) {
  checksumLines.push(`${await sha256(path.join(releaseDirectory, name))}  ${name}`);
}
await writeFile(
  path.join(releaseDirectory, "SHA256SUMS"),
  `${checksumLines.join("\n")}\n`,
  "utf8",
);

console.log(`Trowel release artifacts prepared at ${releaseDirectory}`);
