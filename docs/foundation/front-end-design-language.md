# Trowel 前端设计语言

本文件约束生产界面的共享视觉与交互。页面可以组合不同业务状态，不能为同一种控件
另画一套相似实现。

## 事实源

- 色彩、字体、圆角、阴影、间距和层级只从 `web/src/styles/tokens.css` 读取；
- 普通正文使用 `--font-sans`，产品标题和中文选择项标题使用 `--font-display`，路径、
  模型、运行状态和短标签使用 `--font-mono`；
- Agent 会话配置使用 `NewSessionDialog`、`RuntimeSelector`、`RuntimeSettings`、
  `SessionPreferences` 和 `ConnectionSessionEditor` 的结构与类名；
- 会话或参与者摘要复用 `cc-multibar__*` 的信息层级，不复制一套近似卡片；
- 工作区选择只使用 `WorkspaceChooser` 和 `WorkdirPicker`；
- 单选下拉框只使用 `PopperSelect`。

## 浮层与下拉框

对话框使用 `--layer-dialog`，在对话框上继续打开的工作区选择器使用
`--layer-nested-dialog`，下拉菜单使用 `--layer-popper`。浮层组件通过 portal 挂到
`document.body`，不能依赖调用页面的 DOM 顺序碰巧盖在上面。

`PopperSelect` 固定锚定在触发框下方，触发框保持原位和完整可见，菜单从下边缘留出
6px 后向下展开。当前选中项只影响内容，不参与菜单定位；空间不足时菜单内部滚动，
不能覆盖触发框或改用浏览器原生 `<select>`。

## 原生浏览器外观

生产 JSX 禁止渲染原生 `<select>` 和 `<option>`。文件选择必须隐藏原生
`input[type=file]`，由 Trowel 按钮触发系统文件选择器。新增控件优先扩展共享组件，
不能直接暴露浏览器的 Choose File、默认箭头、默认按钮字体或平台不一致的外观。
确认、警告和输入弹窗不能调用 `window.confirm/alert/prompt`，统一使用 Trowel 对话框。

`cd web && bun run check:ui-contracts` 是对应 Gate。它不能替代截图和真实浏览器检查，
但会阻止这两类已知退化重新进入生产代码。
