# macOS 发布流程

正式版本只从 `main` 上指向当前版本的 tag 构建。`make:mac:community` 和
`TROWEL_RELEASE_BUILD=1` 都会检查分支、tag，以及 `trowel_py/`、`web/` 和依赖
清单中已跟踪与未跟踪的源码变化；任一条件不符都会停止构建。构建输入之外的本机
日志和工作文件不会进入安装包，也不影响源码门禁。

## 开发桌面版

首次准备依赖：

```bash
uv sync --locked --group dev
cd web
bun install --frozen-lockfile
```

日常开发使用 Vite renderer、源码 sidecar 和 Electron Host：

```bash
cd web
bun run desktop:dev
```

日常开发默认与正式 App 共用唯一长期数据根：

```text
~/Library/Application Support/Trowel/data
```

正式 App 必须先完整退出；数据根的进程锁会拒绝第二个 sidecar。开发代码如果包含
正式 App 尚未应用的数据库 migration，也会拒绝打开长期数据，避免旧 App 无法回读。
长期数据尚未初始化时同样必须先启动正式 App；canonical dev 不负责创建正式 schema。
只做 UI、迁移或故障实验时显式使用隔离沙箱：

```bash
cd web
bun run desktop:dev:isolated
```

隔离数据位于 `~/Library/Application Support/Trowel Dev/data`，不会自动回写长期
Profile、Memory 或数据库。开发启动器仍会清除父 Trowel Desktop 的实例、凭据、
端口、数据和日志变量，但保留 `HOME`、`PATH`、`CODEX_HOME` 和显式 Python 路径等
runtime 环境。

从发布前旧开发布局迁移时，先保持 Trowel 运行并只看 plan；退出 App 后重新执行
plan，确认 `ready=true` 才允许 apply：

```bash
cd web
bun run desktop:data-migration:plan
bun run desktop:data-migration:apply
```

迁移器要求 lifecycle snapshot 与 `resource-exit.json` 证明同一 App 实例已干净退出，
并在原子发布前复核实例没有变化。它复制旧 Memory/Profile，合并当前候选新增的
journal、标题和工作区，显式导入仓库 `config.toml`，但不导入旧开发
`trowel.db`，因此旧 Garden 不迁移。SQLite 通过 Backup API 复制并执行
`quick_check`，结果先在 staging 校验再原子发布；旧来源不会删除。

品牌源图位于 `web/public/brand/trowel-mark.svg`，应用图标的底色、边距和麦芽尺寸
位于 `web/scripts/generate-brand-assets.mjs`。修改后可单独生成预览：

```bash
cd web
bun run brand:build
```

## 本地候选包

只生成 `.app`：

```bash
cd web
bun run package:mac
open out/Trowel-darwin-arm64/Trowel.app
```

`package:mac` 只重建 `web/out/Trowel-darwin-arm64/Trowel.app`，不会更新
`web/out/release/`。后者只有执行 `make:mac` 或 `make:mac:community` 后才会重新生成，
因此不能把较早构建的 release 目录与较新的 `.app` 混作同一候选。

生成 `.app`、DMG、zip、依赖清单、Release 文本和 SHA-256：

```bash
cd web
bun run make:mac
bun run smoke:packaged-mac
bun run smoke:packaged-residency-mac
bun run smoke:dmg-install-mac
```

`make:mac` 会创建锁定的 Python 3.13.12 构建环境并冻结 sidecar，不使用系统 Python
运行最终应用。本地没有 Developer ID 时产物使用 ad-hoc 签名，可作为首版开源
候选；`release-manifest.json` 会如实记录 `signing: "ad-hoc"` 和
`notarized: false`。

ad-hoc 候选没有 Apple Developer ID 和公证票据，`spctl` 会将 App 与 DMG 判定为
`rejected`。首版 Release Notes 必须直接说明这一限制；完成来源和 SHA-256 核对后，
首次打开按 Apple 的
[隐私与安全性说明](https://support.apple.com/guide/mac-help/open-a-mac-app-from-an-unidentified-developer-mh40616/mac)
操作。

Trowel 不用 Cookie 保存登录态。正式包关闭 Electron Cookie 加密，启动时清除旧
Cookie，并拒收响应中的 Cookie 头，因此正常运行不会为 `Trowel Safe Storage`
访问 macOS 登录钥匙串。`localStorage` 中的 renderer 偏好不受影响。自动 smoke 的
`use-mock-keychain` 只隔离测试用户，不能作为正式包不弹钥匙串提示的证据；正式候选
还要在锁定登录钥匙串的 Tart VM 中确认 Cookie 为 0、没有 Safe Storage 调用且能
完整退出。

首版 ad-hoc 开源候选仍必须从合入 `main` 的 `v<版本>` tag 构建，通过后端、公开
契约、前端、整包、DMG、干净环境和双 runtime Gate，并由用户审核 Draft Release
后亲自 Publish。使用独立 community 命令启用源码门禁，但不启用 Apple 服务：

```bash
cd web
bun run make:mac:community
```

## Developer ID 发布前置条件

- `pyproject.toml` 与 `web/package.json` 使用相同版本；
- Developer ID Application 证书已导入当前钥匙串；
- `notarytool` 凭据已保存为钥匙串 profile；
- 当前 commit 已通过 Pull Request 合入 `main`，并创建 `v<版本>` tag；
- 后端、公开契约、前端和 macOS package Gate 全部通过。

加入 Apple Developer Program 后，在 Certificates, Identifiers & Profiles 中创建
`Developer ID Application` 证书并导入登录钥匙串。随后确认系统能找到签名身份：

```bash
security find-identity -v -p codesigning
```

公证凭据可使用 Apple ID 的 app-specific password 保存到当前用户钥匙串：

```bash
xcrun notarytool store-credentials trowel-notary \
  --apple-id '<Apple ID>' \
  --team-id '<Team ID>'
```

命令会安全提示输入 app-specific password。也可以使用 App Store Connect API key，
通过 `--key`、`--key-id` 和 `--issuer` 保存同名 profile；私钥和密码不能写入仓库。

## 构建 Developer ID 候选包

```bash
cd web
TROWEL_RELEASE_BUILD=1 \
TROWEL_CODESIGN_IDENTITY="Developer ID Application: ..." \
TROWEL_NOTARYTOOL_KEYCHAIN_PROFILE="trowel-notary" \
bun run make:mac
bun run smoke:packaged-mac
```

正式构建成功后验证签名、公证票据和 Gatekeeper：

```bash
codesign --verify --deep --strict --verbose=2 out/Trowel-darwin-arm64/Trowel.app
xcrun stapler validate out/Trowel-darwin-arm64/Trowel.app
xcrun stapler validate out/release/Trowel-0.2.0-macos-arm64.dmg
spctl --assess --type execute --verbose=4 out/Trowel-darwin-arm64/Trowel.app
spctl --assess --type open --context context:primary-signature --verbose=4 \
  out/release/Trowel-0.2.0-macos-arm64.dmg
```

候选产物统一放在 `web/out/release/`：

- `.dmg` 和 `.zip`；
- `SHA256SUMS`；
- `DEPENDENCIES.txt`；
- `release-manifest.json`；
- `RELEASE_NOTES.md`。

## 双 runtime Gate

在 Tart 全新 VM 中先不安装 Claude Code 和 Codex，确认 Trowel 能启动，两个 runtime
均显示安装提示且其他页面可用。随后分别安装两个 CLI；每次安装后完整退出并重启
Trowel，让启动期探测重新执行。

首版不登录私人账号。在一个不含凭据的普通临时目录中分别验证 Claude Code 和
Codex：

1. 创建会话并发送最小提示，确认 Trowel 真正启动对应 CLI；
2. Claude Code 明确进入 `Not logged in · Please run /login`，Codex 明确产生用户输入、
   turn 启动、状态和中断事件；
3. 执行中断和关闭，确认 UI 结束 turn，会话资源为 `closed / 0`；
4. 退出 Trowel，确认没有 Trowel 所有的 runtime、MCP、watcher、sidecar 或 Electron
   helper 残留；
5. 分别移除一个 CLI，确认缺失方只显示安装提示，另一方仍可启动。

登录后的真实回复、退出重启恢复和原生历史一致性是后续增强 Gate，不阻塞首版开源
包。以后执行登录 Gate 时，凭据只在一次性 VM 副本内交互输入，不写入母盘、日志、
截图或 Release 附件。Gate 记录只保存 Trowel 版本、CLI 版本、场景结果和去身份化
诊断信息。

GitHub `macos-26` workflow 由 GitHub 提供临时 Apple Silicon runner，负责从干净
checkout 重建候选包并运行自动 smoke。它证明构建不依赖开发机，但不能替代真实
桌面交互、双 runtime 进程生命周期、Developer ID、公证和 Gatekeeper 人工验证。

## Draft Release

先完成 Tart VM、安装、覆盖升级和双 runtime smoke，并把结果填入
`RELEASE_NOTES.md`；发布 Developer ID 版本时再增加公证和 Gatekeeper。随后从同一
tag 创建 GitHub Draft Release，上传 `web/out/release/` 内全部文件。Release 保持
Draft，直到文本、manifest、哈希和兼容性记录完成人工复核后再 Publish。

首个公开版本标记为 GitHub Pre-release。它采用面向首版的“核心能力、安装、兼容性
与已知限制、发布校验、相关变更、附件”结构，不生成没有上一版本基线的 Full
Changelog，也不保留空的功能、修复或贡献者分组。

## 首版 Pre-release 发布顺序

1. `m11/l08-macos-release` 通过 Pull Request 合入 `memory`；
2. `memory` 通过 Milestone Pull Request squash 合入 `main`；
3. 本地快进到远端 `main`，在合入 commit 上创建并推送 annotated tag；
4. 从 `v0.2.0` 运行 GitHub `macos-package.yml`，确认干净 Apple Silicon runner 的
   构建和 smoke 全绿；
5. 回到干净的 `main`，从同一 tag 重建正式 community 附件并复验；
6. 创建 Draft Pre-release，人工复核后再 Publish。

```bash
git switch main
git fetch origin
git merge --ff-only origin/main
git tag -a v0.2.0 -m "Trowel v0.2.0"
git push origin v0.2.0

gh workflow run macos-package.yml --ref v0.2.0
gh run watch

cd web
bun run make:mac:community
bun run smoke:packaged-mac
bun run smoke:packaged-residency-mac
bun run smoke:dmg-install-mac
(cd out/release && shasum -a 256 -c SHA256SUMS)
jq '.source, .signing, .notarized' out/release/release-manifest.json

gh release create v0.2.0 out/release/* \
  --draft \
  --prerelease \
  --verify-tag \
  --title "Trowel v0.2.0" \
  --notes-file out/release/RELEASE_NOTES.md
```

正式 community manifest 应记录 `branch: "main"`、`tracked_changes: false`、
`build_source_changes: false`、`version_tag_at_head: true`、`signing: "ad-hoc"` 和
`notarized: false`。Draft 页面中的版本、兼容性、附件名和 SHA-256 与 manifest 一致
后，最终 Publish 由发布人手动完成。
