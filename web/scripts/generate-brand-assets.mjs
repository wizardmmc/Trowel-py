/** 从唯一麦芽 SVG 生成 renderer、Tray 和 macOS 安装包使用的位图资产。 */

import { execFileSync } from "node:child_process";
import { cp, mkdir, rm } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import sharp from "sharp";

const webRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const markPath = path.join(webRoot, "public", "brand", "trowel-mark.svg");
const desktopAssets = path.join(webRoot, "desktop", "assets");
const buildAssets = path.join(webRoot, "build");
const iconPng = path.join(buildAssets, "icon.png");
const iconset = path.join(buildAssets, "Trowel.iconset");
const applicationIconSize = 1024;
const applicationMarkSize = 600;
const applicationMarkOffset = (applicationIconSize - applicationMarkSize) / 2;

await mkdir(desktopAssets, { recursive: true });
await mkdir(buildAssets, { recursive: true });

/** 渲染菜单栏使用的透明麦芽，并保留 Electron 的 Retina 命名约定。 */
async function generateStatusIcons() {
  await sharp(markPath)
    .resize(18, 18, { fit: "contain" })
    .png()
    .toFile(path.join(desktopAssets, "status-icon.png"));
  await sharp(markPath)
    .resize(36, 36, { fit: "contain" })
    .png()
    .toFile(path.join(desktopAssets, "status-icon@2x.png"));
}

/** 生成深绿圆角底和金色麦芽组成的 1024px 主应用图标。 */
async function generateApplicationPng() {
  const background = Buffer.from(`
    <svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024">
      <rect x="64" y="64" width="896" height="896" rx="208" fill="#2D3B2D"/>
      <rect x="72" y="72" width="880" height="880" rx="200"
            fill="none" stroke="#E8B84B" stroke-opacity="0.34" stroke-width="8"/>
    </svg>
  `);
  const mark = await sharp(markPath)
    .resize(applicationMarkSize, applicationMarkSize, { fit: "contain" })
    .png()
    .toBuffer();
  await sharp(background)
    .composite([
      { input: mark, left: applicationMarkOffset, top: applicationMarkOffset },
    ])
    .png()
    .toFile(iconPng);
}

/** 在 macOS 上把标准 iconset 封装成 Electron Packager 使用的 ``.icns``。 */
async function generateMacIcon() {
  if (process.platform !== "darwin") return;
  await rm(iconset, { recursive: true, force: true });
  await mkdir(iconset, { recursive: true });
  const variants = [
    [16, "icon_16x16.png"],
    [32, "icon_16x16@2x.png"],
    [32, "icon_32x32.png"],
    [64, "icon_32x32@2x.png"],
    [128, "icon_128x128.png"],
    [256, "icon_128x128@2x.png"],
    [256, "icon_256x256.png"],
    [512, "icon_256x256@2x.png"],
    [512, "icon_512x512.png"],
    [1024, "icon_512x512@2x.png"],
  ];
  await Promise.all(
    variants.map(([size, name]) =>
      sharp(iconPng)
        .resize(Number(size), Number(size))
        .png()
        .toFile(path.join(iconset, String(name))),
    ),
  );
  execFileSync("iconutil", [
    "-c",
    "icns",
    "-o",
    path.join(buildAssets, "Trowel.icns"),
    iconset,
  ]);
  await rm(iconset, { recursive: true, force: true });
}

await generateStatusIcons();
await generateApplicationPng();
await generateMacIcon();
await cp(iconPng, path.join(webRoot, "public", "favicon.png"));
