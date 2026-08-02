/** 配置 Trowel 的 Electron 打包、macOS 安装介质和正式发布门禁。 */

const path = require("node:path");
const { FusesPlugin } = require("@electron-forge/plugin-fuses");
const { FuseV1Options, FuseVersion } = require("@electron/fuses");
const { validateReleaseSource } = require("./scripts/release-source.cjs");

const webRoot = __dirname;
const releaseBuild = process.env.TROWEL_RELEASE_BUILD === "1";
const releaseSourceGate =
  releaseBuild || process.env.TROWEL_RELEASE_SOURCE === "1";
const entitlements = path.join(webRoot, "packaging", "entitlements.mac.plist");
const sidecarEntitlements = path.join(
  webRoot,
  "packaging",
  "entitlements.sidecar.plist",
);

/** production 配置缺少签名事实时立即失败，不能静默产出未签名发布包。 */
function requiredReleaseEnvironment(name) {
  const value = process.env[name]?.trim();
  if (!value) throw new Error(`${name} is required for TROWEL_RELEASE_BUILD=1`);
  return value;
}

if (releaseSourceGate) {
  validateReleaseSource({
    projectRoot: path.resolve(webRoot, ".."),
    version: require("./package.json").version,
  });
}

/** 本地统一重做 ad-hoc 签名；正式发布切换 Developer ID 并启用公证。 */
function macSecurityOptions() {
  const options = {
    osxSign: {
      identity: releaseBuild
        ? requiredReleaseEnvironment("TROWEL_CODESIGN_IDENTITY")
        : "-",
      identityValidation: releaseBuild,
      continueOnError: false,
      optionsForFile: (filePath) => ({
        entitlements: filePath.includes(
          `${path.sep}Resources${path.sep}sidecar${path.sep}`,
        )
          ? sidecarEntitlements
          : entitlements,
        hardenedRuntime: true,
      }),
    },
  };
  if (!releaseBuild) return options;
  return {
    ...options,
    osxNotarize: {
      tool: "notarytool",
      keychainProfile: requiredReleaseEnvironment(
        "TROWEL_NOTARYTOOL_KEYCHAIN_PROFILE",
      ),
    },
  };
}

module.exports = {
  packagerConfig: {
    name: "Trowel",
    executableName: "Trowel",
    appBundleId: "io.github.wizardmmc.trowel",
    appCategoryType: "public.app-category.developer-tools",
    icon: path.join(webRoot, "build", "Trowel.icns"),
    asar: true,
    download: {
      checksums: {
        "electron-v43.2.0-darwin-arm64.zip":
          "ad4a0ae3c37ee05aa06c7e2ed0627608389790f0505a2b0d20319efbe33ffe28",
      },
    },
    extraResource: [path.join(webRoot, ".release", "sidecar")],
    ignore: (filePath) => {
      if (!filePath) return false;
      return !["/package.json", "/desktop-dist", "/dist"].some(
        (allowed) => filePath === allowed || filePath.startsWith(`${allowed}/`),
      );
    },
    ...macSecurityOptions(),
  },
  rebuildConfig: {},
  makers: [
    {
      name: "@electron-forge/maker-dmg",
      config: {
        name: "Trowel",
        format: "ULFO",
      },
    },
    {
      name: "@electron-forge/maker-zip",
      platforms: ["darwin"],
    },
  ],
  plugins: [
    new FusesPlugin({
      version: FuseVersion.V1,
      [FuseV1Options.RunAsNode]: false,
      // Trowel 保留 localStorage 偏好，但 Electron Host 明确清理并拒收 Cookie。
      [FuseV1Options.EnableCookieEncryption]: false,
      [FuseV1Options.EnableNodeOptionsEnvironmentVariable]: false,
      [FuseV1Options.EnableNodeCliInspectArguments]: false,
      [FuseV1Options.EnableEmbeddedAsarIntegrityValidation]: true,
      [FuseV1Options.OnlyLoadAppFromAsar]: true,
    }),
  ],
};
