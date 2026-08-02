/** 挂载 DMG、覆盖临时安装目录并验证应用数据跨安装保留。 */

import { execFile } from "node:child_process";
import { access, mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { promisify } from "node:util";
import { fileURLToPath } from "node:url";

const execute = promisify(execFile);
const webRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const packageJson = JSON.parse(await readFile(path.join(webRoot, "package.json"), "utf8"));
const dmgPath = path.join(
  webRoot,
  "out",
  "release",
  `Trowel-${packageJson.version}-macos-arm64.dmg`,
);
const testRoot = await mkdtemp(path.join(os.tmpdir(), "trowel-dmg-install-"));
const mountPoint = path.join(testRoot, "mounted");
const installDirectory = path.join(testRoot, "Applications");
const installedApp = path.join(installDirectory, "Trowel.app");
const smokeRoot = path.join(testRoot, "smoke-data");
let attached = false;
let passed = false;

/** 运行外部命令并保留足够输出供失败诊断。 */
async function run(command, args, options = {}) {
  return execute(command, args, {
    cwd: webRoot,
    encoding: "utf8",
    maxBuffer: 16 * 1024 * 1024,
    ...options,
  });
}

/** 用指定临时安装副本执行完整 packaged smoke。 */
async function runInstalledSmoke() {
  const executable = path.join(installedApp, "Contents", "MacOS", "Trowel");
  await run(process.execPath, [path.join(webRoot, "scripts", "smoke-packaged-app.mjs")], {
    env: {
      ...process.env,
      TROWEL_PACKAGED_APP_EXECUTABLE: executable,
      TROWEL_PACKAGED_SMOKE_ROOT: smokeRoot,
      TROWEL_PACKAGED_SMOKE_PRESERVE: "1",
    },
  });
}

try {
  await mkdir(mountPoint, { recursive: true });
  await mkdir(installDirectory, { recursive: true });
  await run("hdiutil", [
    "attach",
    "-nobrowse",
    "-readonly",
    "-mountpoint",
    mountPoint,
    dmgPath,
  ]);
  attached = true;
  await access(path.join(mountPoint, "Trowel.app"));
  await access(path.join(mountPoint, "Applications"));

  await run("ditto", [path.join(mountPoint, "Trowel.app"), installedApp]);
  await run("codesign", ["--verify", "--deep", "--strict", installedApp]);
  await runInstalledSmoke();

  const markerPath = path.join(smokeRoot, "data", "upgrade-marker.txt");
  await writeFile(markerPath, "preserve-across-overwrite\n", "utf8");
  await rm(installedApp, { recursive: true, force: true });
  await run("ditto", [path.join(mountPoint, "Trowel.app"), installedApp]);
  await runInstalledSmoke();
  if ((await readFile(markerPath, "utf8")) !== "preserve-across-overwrite\n") {
    throw new Error("overwriting Trowel.app changed the application data marker");
  }

  passed = true;
  console.log("TROWEL_DMG_INSTALL_AND_OVERWRITE_SMOKE_OK");
} finally {
  if (attached) {
    await run("hdiutil", ["detach", mountPoint]).catch(() => undefined);
  }
  if (passed) await rm(testRoot, { recursive: true, force: true });
  else console.error(`DMG smoke files kept at ${testRoot}`);
}
