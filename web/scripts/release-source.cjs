/** 校验 macOS 正式发布使用干净的 main 版本 tag 作为源码。 */

const { execFileSync } = require("node:child_process");
const path = require("node:path");

const BUILD_SOURCE_PATHS = Object.freeze([
  "pyproject.toml",
  "uv.lock",
  "trowel_py",
  "web",
]);

/** 根据已读取的 Git 事实拒绝不满足正式发布要求的源码。 */
function assertReleaseSourceFacts({ branch, sourceChanges, tags, version }) {
  if (branch !== "main") throw new Error("release source requires the main branch");
  if (sourceChanges) {
    throw new Error("release source requires clean build source paths");
  }
  if (!tags.includes(`v${version}`)) {
    throw new Error(`release source requires tag v${version} at HEAD`);
  }
}

/** 从 Git 读取正式发布的分支、源码改动和版本 tag 并执行门禁。 */
function validateReleaseSource({ projectRoot, version }) {
  const runGit = (args) =>
    execFileSync("git", args, { cwd: projectRoot, encoding: "utf8" }).trim();
  assertReleaseSourceFacts({
    branch: runGit(["branch", "--show-current"]),
    sourceChanges: runGit([
      "status",
      "--porcelain",
      "--untracked-files=all",
      "--",
      ...BUILD_SOURCE_PATHS,
    ]),
    tags: runGit(["tag", "--points-at", "HEAD"]).split("\n").filter(Boolean),
    version,
  });
}

module.exports = {
  assertReleaseSourceFacts,
  validateReleaseSource,
};

if (require.main === module) {
  const projectRoot = path.resolve(__dirname, "..", "..");
  validateReleaseSource({
    projectRoot,
    version: require(path.join(projectRoot, "web", "package.json")).version,
  });
}
