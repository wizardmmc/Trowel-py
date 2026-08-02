# Trowel v0.2.0

这是 Trowel 的首个公开 macOS 预览版。Trowel 在桌面端托管 Claude Code 与 Codex
会话，把经过验证、可追溯的信息沉淀为长期记忆，并提供复习卡片和知识花园。

## 核心能力

- 统一管理 Claude Code 与 Codex 会话，支持消息、工具调用、提问、历史恢复和多开；
- 从真实会话中提炼日记与笔记，并在后续会话中注入相关 Memory 和 Profile；
- 将会话或粘贴内容整理为复习卡片，并通过知识花园展示复习状态；
- 提供正式的 Trowel 应用名称、金色麦芽应用图标和系统状态菜单；
- 关闭窗口后保留 sidecar、后台任务和 Agent 会话，可从 Dock 或状态菜单恢复；
- Electron Host、React 界面和 Python sidecar 一并打包，不依赖系统 Python、
  Node 或 Bun；
- Trowel 长期数据统一保存在稳定的应用数据目录；旧开发 Memory/Profile 经离线
  迁移后继续可用，覆盖安装不会清除会话、Memory 或配置；
- 自动识别本机 Claude Code 与 Codex CLI。缺少其中一个时仅停用对应 runtime；
- 增加安装包资源收敛、全新数据库初始化、依赖清单和 SHA-256 校验。

## 安装与升级

1. 下载 `Trowel-0.2.0-macos-arm64.dmg`；
2. 使用附件中的 `SHA256SUMS` 核对 DMG 的 SHA-256；
3. 打开 DMG，将 Trowel 拖入“应用程序”；
4. 升级时先退出正在运行的 Trowel，再用新版覆盖“应用程序”中的旧版。

```bash
shasum -a 256 Trowel-0.2.0-macos-arm64.dmg
```

当前预览包使用 ad-hoc 签名，尚未完成 Apple Developer ID 签名和公证。macOS 会阻止
普通首次打开；确认下载来源和 SHA-256 后，可先尝试打开 Trowel，再到“系统设置 >
隐私与安全性”按 Apple 的
[安全设置说明](https://support.apple.com/guide/mac-help/open-a-mac-app-from-an-unidentified-developer-mh40616/mac)
选择仍要打开。

## 兼容性与已知限制

- 当前支持 macOS 26.5 和 Apple Silicon；Intel macOS、Windows 和 Linux 尚未发布；
- Claude Code 与 Codex CLI 不包含在安装包中，缺少其中一个时只停用对应 runtime；
- 应用内自动更新尚未接入，后续版本继续通过 GitHub Release 覆盖安装；
- 当前版本标记为 Pre-release，Developer ID 签名和公证尚未完成。

## 发布校验

- 后端 3455 项通过、2 项跳过、22 项集成测试未默认执行；公开契约 5 项、前端类型
  检查、134 个测试文件中的 981 项测试和 production build 通过；
- 普通启动、诊断、单实例、sidecar hang、renderer crash 和共享 Agent Service
  六条 Desktop Host smoke 通过；
- 当前候选通过整包启动、关窗后台驻留、DMG 临时安装和覆盖安装 smoke；
- Tart 全新 macOS 26.5 VM 完成 DMG 安装、Finder 启动、锁定钥匙串、renderer
  crash 恢复和资源归零验证；
- VM 安装 Claude Code 与 Codex 后，分别进入明确的认证前状态并可中断、关闭；
  本次没有登录私人账号；
- `.app` 深度签名结构、DMG 完整性和发布附件本机路径扫描通过；
- 最新源码构建的 `.app` 已通过真实会话关闭、普通整包和关窗驻留复验；
- SHA-256：见随附件提供的 `SHA256SUMS`；
- 依赖清单：见随附件提供的 `DEPENDENCIES.txt`。

## 相关变更

- 超大 turn 的流式渲染和历史回放：[#78](https://github.com/wizardmmc/Trowel-py/pull/78)；
- Agent 前端所有权和长内容展示：[#79](https://github.com/wizardmmc/Trowel-py/pull/79)；
- Claude Code 与 Codex capability 边界：[#80](https://github.com/wizardmmc/Trowel-py/pull/80)；
- Electron Host、共享 Agent Service 与工作区外壳：[#81](https://github.com/wizardmmc/Trowel-py/pull/81)；
- 会话、退出和崩溃后的资源收敛：[#82](https://github.com/wizardmmc/Trowel-py/pull/82)。

## 附件

- `Trowel-0.2.0-macos-arm64.dmg`：推荐安装介质；
- `Trowel-0.2.0-macos-arm64.zip`：App 压缩包；
- `SHA256SUMS`：全部发布附件的 SHA-256；
- `DEPENDENCIES.txt`：Python、Electron 和前端依赖清单；
- `release-manifest.json`：源码 commit、签名、公证和产物事实；
- `RELEASE_NOTES.md`：本页发布说明的附件副本。
