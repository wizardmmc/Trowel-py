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
- 单选下拉框只使用 `PopperSelect`；
- ECharts 等数据图表从对应的 CSS 图表色板读取颜色，业务 TSX 不保存第二份颜色常量。

## 颜色与 token

产品 CSS 和 TSX 不直接写具体颜色，统一引用按用途命名的设计 token。Trowel 自有设计
token 不带 fallback；缺失或改名必须直接暴露，不能由备用色值静默掩盖。第三方组件或
运行时变量确实存在“尚未提供”的状态时，可以按精确文件和变量名登记 fallback owner，
并写明责任方和原因。

全局设计 token 由 `web/src/styles/tokens.css` 负责。图表独有颜色由对应的 CSS 色板负责，
普通页面不能借用图表色板。SVG 资产、第三方接口等必须使用具体颜色的边界按精确文件
登记 owner，不能豁免整个目录。

## 浮层与下拉框

对话框使用 `--layer-dialog`，在对话框上继续打开的工作区选择器使用
`--layer-nested-dialog`，下拉菜单使用 `--layer-popper`。浮层组件通过 portal 挂到
`document.body`，不能依赖调用页面的 DOM 顺序碰巧盖在上面。

页面级 portal、全屏遮罩和跨页面浮层使用全局 `--layer-*` token。组件内部需要排序时，
先用 `isolation: isolate` 建立独立层叠范围；简单前后关系使用 `0` 和 `1`，更多层级使用
该组件自己命名的变量。业务样式不能新增负层级、任意大数字或借用无关的全局浮层 token。

`PopperSelect` 固定锚定在触发框下方，触发框保持原位和完整可见，菜单从下边缘留出
6px 后向下展开。当前选中项只影响内容，不参与菜单定位；空间不足时菜单内部滚动，
不能覆盖触发框或改用浏览器原生 `<select>`。

## 原生浏览器外观

生产 JSX 禁止渲染原生 `<select>` 和 `<option>`。文件选择必须隐藏原生
`input[type=file]`，由 Trowel 按钮触发系统文件选择器。新增控件优先扩展共享组件，
不能直接暴露浏览器的 Choose File、默认箭头、默认按钮字体或平台不一致的外观。
确认、警告和输入弹窗不能调用 `window.confirm/alert/prompt`，统一使用 Trowel 对话框。
除 `PopperSelect` 自身外，业务组件不能直接导入 `@radix-ui/react-select`。Switch 和通用
Dialog 尚未冻结成全局 primitive，重复候选只报告，不作为失败。

`cd web && bun run check:ui-contracts` 使用 CSS 和 TypeScript AST 检查上述客观边界。
合法边界记录在 `web/scripts/ui-contracts/project-policy.mjs`；尚未安全修复的存量按规则、
精确文件和结构身份记录在 `web/scripts/ui-contracts/baseline.json`，只能减少，不能用于
接受新增违规。该 Gate 不能替代截图和真实浏览器检查。
