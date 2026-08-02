/** 解析整包 smoke 在默认和显式隔离模式下检查的业务数据目录。 */

import path from "node:path";

export function resolvePackagedSmokeDataDirectory({
  defaultPathsSmoke,
  homeDirectory,
  smokeRoot,
}) {
  return defaultPathsSmoke
    ? path.join(homeDirectory, "Library", "Application Support", "Trowel", "data")
    : path.join(smokeRoot, "data");
}
