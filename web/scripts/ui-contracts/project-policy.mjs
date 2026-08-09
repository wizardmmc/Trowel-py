/** 声明 Trowel 前端颜色与局部层级的精确合法 owner。 */

export const UI_CONTRACT_POLICY = Object.freeze({
  fallbackOwners: Object.freeze([
    Object.freeze({
      path: "src/agent/ui/agent.css",
      variable: "--composer-h",
      owner: "Agent Composer 实时高度",
      reason: "首次测量前使用 0px，随后由 SessionView 写入实际高度。",
    }),
    Object.freeze({
      path: "src/agent/ui/agent.css",
      variable: "--agent-multi-width",
      owner: "AppLayout 桌面栏宽度",
      reason: "脱离 AppLayout 的性能与组件场景使用同值默认宽度。",
    }),
    Object.freeze({
      path: "src/components/review/ReviewSession.css",
      variable: "--app-top-drag-height",
      owner: "AppLayout 桌面拖拽区高度",
      reason: "Review 独立覆盖层脱离 AppLayout 时保持固定拖拽区高度。",
    }),
    Object.freeze({
      path: "src/settings/ui/settings-workspace.css",
      variable: "--app-top-drag-height",
      owner: "AppLayout 桌面拖拽区高度",
      reason: "设置工作区首次独立渲染时保持固定拖拽区高度。",
    }),
    Object.freeze({
      path: "src/components/ui/popper-select.css",
      variable: "--radix-select-content-available-height",
      owner: "Radix Select 可用高度",
      reason: "Radix 尚未完成测量时用视口高度限制菜单。",
    }),
  ]),
  rawColorOwners: Object.freeze([
    Object.freeze({
      path: "src/styles/tokens.css",
      declaration: "custom-property",
      owner: "Trowel 全局设计 token",
      reason: "普通产品 UI 的颜色、阴影和层级由该文件统一定义。",
    }),
    Object.freeze({
      path: "src/statistics/ui/charts/chart-palette.css",
      declaration: "custom-property",
      owner: "Statistics 图表色板",
      reason: "ECharts 图表专用透明度与状态颜色由该文件统一定义。",
    }),
    Object.freeze({
      path: "src/components/pet/PetSVG.tsx",
      owner: "宠物 SVG 资产",
      reason: "插画内部颜色属于可独立检查的视觉资产，不代表普通 UI 语义。",
    }),
    Object.freeze({
      path: "src/components/garden/plants/categoryColors.ts",
      owner: "知识类别色板",
      reason: "类别颜色编码数据类别，不代表普通 UI 语义。",
    }),
  ]),
  globalLayerOwners: Object.freeze([
    Object.freeze({
      path: "src/agent/ui/agent.css",
      selector: ".cc-modal-backdrop",
      variable: "--layer-dialog",
      owner: "Agent 全屏对话框",
      reason: "固定定位的全屏遮罩需要覆盖 Agent 工作区。",
    }),
    Object.freeze({
      path: "src/agent/ui/agent.css",
      selector: ".cc-modal-backdrop--nested",
      variable: "--layer-nested-dialog",
      owner: "Agent 嵌套对话框",
      reason: "工作区选择器需要覆盖已经打开的 Agent 对话框。",
    }),
    Object.freeze({
      path: "src/agent/ui/agent.css",
      selector: ".cc-dialog__backdrop",
      variable: "--layer-dialog",
      owner: "Agent 旧对话框遮罩",
      reason: "固定定位的全屏遮罩需要覆盖 Agent 工作区。",
    }),
    Object.freeze({
      path: "src/components/ui/confirm-dialog.css",
      selector: ".confirm-dialog__backdrop",
      variable: "--layer-nested-dialog",
      owner: "确认对话框",
      reason: "确认操作可能从已有对话框中打开，需要使用嵌套对话框层级。",
    }),
    Object.freeze({
      path: "src/components/ui/popper-select.css",
      selector: ".popper-select__content",
      variable: "--layer-popper",
      owner: "PopperSelect 菜单",
      reason: "Radix portal 菜单需要覆盖触发它的页面和对话框内容。",
    }),
    Object.freeze({
      path: "src/discussion/ui/discussion.css",
      selector: ".discussion-modal-backdrop",
      variable: "--layer-dialog",
      owner: "Discussion 创建对话框遮罩",
      reason: "固定定位的创建对话框遮罩需要覆盖整个 Discussion 工作区。",
    }),
    Object.freeze({
      path: "src/discussion/ui/discussion.css",
      selector: ".discussion-inspector-backdrop",
      variable: "--layer-dialog",
      owner: "Discussion 检查器遮罩",
      reason: "固定定位的检查器遮罩需要覆盖整个 Discussion 工作区。",
    }),
  ]),
  localLayerOwners: Object.freeze([]),
  exceptions: Object.freeze([
    Object.freeze({
      fingerprint:
        "jsx/raw-color|src/components/cc/AssistantText.tsx|property=errorColor|value=#cc0000",
      owner: "KaTeX 渲染适配器",
      reason: "KaTeX errorColor 接口需要具体颜色字符串，不能读取 CSS token。",
    }),
  ]),
});
