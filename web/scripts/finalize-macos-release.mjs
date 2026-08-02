/** 正式发布时签名、公证并 staple 最终 DMG，开发候选不访问 Apple 服务。 */

import { spawn } from "node:child_process";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

/** 返回可审查、可测试的 app 验收与 DMG 发布命令序列。 */
export function buildMacReleaseFinalizationCommands({
  appPath,
  dmgPath,
  identity,
  keychainProfile,
}) {
  return [
    {
      command: "xcrun",
      args: ["stapler", "validate", "-v", appPath],
    },
    {
      command: "codesign",
      args: ["--force", "--sign", identity, "--timestamp", dmgPath],
    },
    {
      command: "codesign",
      args: ["--verify", "--strict", "--verbose=2", dmgPath],
    },
    {
      command: "xcrun",
      args: [
        "notarytool",
        "submit",
        dmgPath,
        "--keychain-profile",
        keychainProfile,
        "--wait",
        "--timeout",
        "20m",
      ],
    },
    {
      command: "xcrun",
      args: ["stapler", "staple", "-v", dmgPath],
    },
    {
      command: "xcrun",
      args: ["stapler", "validate", "-v", dmgPath],
    },
  ];
}

function requiredReleaseEnvironment(name) {
  const value = process.env[name]?.trim();
  if (!value) throw new Error(`${name} is required for TROWEL_RELEASE_BUILD=1`);
  return value;
}

function run({ command, args }) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { stdio: "inherit" });
    child.once("error", reject);
    child.once("exit", (code, signal) => {
      if (code === 0) resolve();
      else reject(new Error(`${command} exited code=${code} signal=${signal}`));
    });
  });
}

async function finalizeRelease() {
  if (process.env.TROWEL_RELEASE_BUILD !== "1") return;

  const webRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
  const commands = buildMacReleaseFinalizationCommands({
    appPath: path.join(webRoot, "out", "Trowel-darwin-arm64", "Trowel.app"),
    dmgPath: path.join(webRoot, "out", "make", "Trowel.dmg"),
    identity: requiredReleaseEnvironment("TROWEL_CODESIGN_IDENTITY"),
    keychainProfile: requiredReleaseEnvironment(
      "TROWEL_NOTARYTOOL_KEYCHAIN_PROFILE",
    ),
  });
  for (const command of commands) await run(command);
}

const invokedPath = process.argv[1] ? path.resolve(process.argv[1]) : null;
if (invokedPath && import.meta.url === pathToFileURL(invokedPath).href) {
  await finalizeRelease();
}
